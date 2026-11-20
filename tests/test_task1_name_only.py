import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from libstruct_bench.cli import run_task1_name_only as run
from libstruct_bench.cli import task1_name_only_sandbox as sandbox

sys.path.insert(0, str(run.ROOT / 'paper/methods'))
import prepare_task1_name_only as analysis


class NameOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Unit tests exercise the same protocol panel using only distributable pins.
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.config_path = Path(directory.name) / 'name_only.json'
        config = run.read(run.DEFAULT_CONFIG)
        config['locked_files'] = [
            entry for entry in config['locked_files'] if not entry['path'].startswith('analysis/')
        ]
        run.write(cls.config_path, config)
        cls.config = run.load_config(cls.config_path)
        cls.protocol = cls.config['protocols'][0]
        cls.model = next(iter(run.MODELS))

    def response(self, libraries=None, **kwargs):
        return {'model': self.model, 'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'libraries': libraries if libraries is not None else []})}}], **kwargs}

    def test_text_only_and_only_name_version_vary(self):
        bodies = []
        for protocol in self.config['protocols']:
            req = run.payload(protocol, self.model)
            self.assertEqual([m['role'] for m in req['messages']], ['system', 'user'])
            self.assertEqual([c['type'] for c in req['messages'][1]['content']], ['text'])
            self.assertEqual(req['tools'], [])
            self.assertEqual(req['tool_choice'], 'none')
            self.assertEqual(req['plugins'], [{'id': p, 'enabled': False} for p in ('web', 'file-parser', 'response-healing')])
            self.assertEqual(req['max_tokens'], 64000)
            self.assertEqual(req['reasoning']['effort'], 'high')
            text = req['messages'][1]['content'][0]['text']
            self.assertTrue(text.startswith('Protocol name: ' + protocol['canonical_name'] + '\n'))
            self.assertNotIn('attached', text)
            self.assertNotIn('Do not answer from protocol name', text)
            self.assertNotIn('ground truth', text)
            self.assertNotIn('input files', text)
            self.assertNotIn('the source', text)
            bodies.append(text.split('\n', 2)[2])
        self.assertEqual(len(set(bodies)), 1)

    def test_sixty_requests_no_downloads_and_no_network_in_dry_run(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(run.api, '_download_input') as download, \
                patch.object(run.api, '_upload_file') as upload, patch.object(run.api, '_post_json') as post:
            out = Path(temp) / 'control'
            with patch('builtins.print'):
                self.assertEqual(run.main(['--protocols', str(self.config_path), '--out', str(out), '--dry-run']), 0)
            self.assertEqual(len(list(out.glob('*/*/request.json'))), 60)
            self.assertEqual(len(list(out.glob('*/*/attempt.json'))), 0)
            download.assert_not_called()
            upload.assert_not_called()
            post.assert_not_called()

    def test_no_overwrite_or_second_call_on_resume(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(run.api, '_post_json', return_value=self.response()) as post:
            out = Path(temp)
            first = run.run_one(out, self.protocol, self.model, 'not-a-key')
            case = out / run.api._slug(self.model) / self.protocol['protocol_id']
            before = {p.name: p.read_bytes() for p in case.iterdir()}
            second = run.run_one(out, self.protocol, self.model, 'not-a-key')
            self.assertEqual(first['status'], 'completed')
            self.assertEqual(second['status'], 'skipped_existing_attempt')
            self.assertEqual(before, {p.name: p.read_bytes() for p in case.iterdir()})
            self.assertEqual(post.call_count, 1)

    def test_gemini_first_then_other_models_in_same_experiment(self):
        gemini = 'google/gemini-3.7-flash'

        def response(url, key, request):
            result = self.response()
            result['model'] = request['model']
            return result

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(run.api, '_post_json', side_effect=response) as post, \
                patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-placeholder'}), \
                patch('builtins.print'):
            out = Path(temp) / 'control'
            self.assertEqual(run.main(['--protocols', str(self.config_path), '--out', str(out), '--model', gemini, '--model', gemini]), 0)
            self.assertEqual(post.call_count, 20)
            self.assertEqual({run.read(p)['model'] for p in out.glob('*/*/request.json')}, {gemini})
            saved = {p: p.read_bytes() for p in (out / run.api._slug(gemini)).glob('*/*') if p.is_file()}
            experiment = (out / 'experiment.json').read_bytes()
            # An unrestricted resume runs just the 40 unstarted cases.
            self.assertEqual(run.main(['--protocols', str(self.config_path), '--out', str(out)]), 0)
            self.assertEqual(post.call_count, 60)
            self.assertEqual(experiment, (out / 'experiment.json').read_bytes())
            self.assertTrue(all(p.read_bytes() == content for p, content in saved.items()))
            summary = run.read(out / 'summary.json')
            self.assertEqual(len(summary), 60)
            self.assertTrue(all(r['status'] == 'completed' for r in summary))

    def test_failed_and_uncertain_attempts_not_retried(self):
        for error in (run.api.OpenRouterResponseError('HTTP 429', status_code=429), KeyboardInterrupt()):
            with tempfile.TemporaryDirectory() as temp, patch.object(run.api, '_post_json', side_effect=error) as post:
                out = Path(temp)
                try:
                    run.run_one(out, self.protocol, self.model, 'not-a-key')
                except KeyboardInterrupt:
                    pass
                self.assertEqual(run.run_one(out, self.protocol, self.model, 'not-a-key')['status'], 'skipped_existing_attempt')
                self.assertEqual(post.call_count, 1)

    def test_prevent_cross_condition_and_changed_experiment(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / 'prediction.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'nonempty'):
                run.prepare_directory(out, self.config)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            run.prepare_directory(out, self.config)
            different = copy.deepcopy(self.config)
            different['protocols'][0]['version'] = 'wrong version'
            with self.assertRaisesRegex(ValueError, 'lock differs'):
                run.prepare_directory(out, different)

    def test_response_classification(self):
        examples = []
        examples.append(({'error': {'code': 504}}, 'technical_failure'))
        wrong = self.response(); wrong['model'] = 'other-model'
        examples.append((wrong, 'condition_violation'))
        tool = self.response(); tool['choices'][0]['message']['tool_calls'] = [{'name': 'web'}]
        examples.append((tool, 'condition_violation'))
        cite = self.response(); cite['choices'][0]['message']['annotations'] = [{'type': 'url_citation'}]
        examples.append((cite, 'condition_violation'))
        truncated = self.response(); truncated['choices'][0]['finish_reason'] = 'length'
        examples.append((truncated, 'invalid_output'))
        malformed = self.response(); malformed['choices'][0]['message']['content'] = 'not JSON'
        examples.append((malformed, 'invalid_output'))
        examples.append((self.response(), 'completed'))
        for response, status in examples:
            with self.subTest(status=status):
                result, _, _ = run.interpret_response(response, self.model, 'test')
                self.assertEqual(result['status'], status)

    def test_existing_pdf_rescoring_matches_locked_scores(self):
        if not (run.ROOT / self.config['reference_lock']).is_file():
            self.skipTest('historical rescoring requires the private reference lock and original runs')
        historical = run.load_config()
        variants, lock = analysis.references(historical)
        results, _ = analysis.pdf_baseline(historical, variants)
        self.assertEqual(len(results), 60)
        self.assertIn('human-vn-w1-barcode-range', lock['snapshot'])
        self.assertTrue(all(results[(m, 'indrop_v1')]['exact'] for m in run.MODELS))

    def test_exact_match_requires_all_libraries_and_no_extra(self):
        metrics = {'sequence_similarity': 1., 'ground_truth_library_count': 2, 'predicted_library_count': 1}
        audit = {'library_matches': [{'predicted_index': 0, 'edit_distance': 0}, {'predicted_index': None, 'edit_distance': 0}]}
        self.assertFalse(analysis.is_exact(metrics, audit))
        metrics['predicted_library_count'] = 2
        self.assertFalse(analysis.is_exact(metrics, audit))
        audit['library_matches'][1]['predicted_index'] = 1
        self.assertTrue(analysis.is_exact(metrics, audit))
        metrics['predicted_library_count'] = 3
        self.assertFalse(analysis.is_exact(metrics, audit))

    def test_missing_abstention_invalid_and_failure_denominators(self):
        pid = self.protocol['protocol_id']
        variants = [{'document': {'protocol_id': pid, 'library_sequence': 'ACGT'}, 'length_choices': []}]
        rows = []
        for status, prediction in [('completed', {'schema_version': 'libstruct.library_structure.v0', 'protocol_id': pid, 'libraries': []}),
                                   ('invalid_output', None), ('technical_failure', None), ('missing', None),
                                   ('interrupted', None), ('condition_violation', None)]:
            name, _ = analysis.score(prediction, variants, pid, status)
            pdf = {'status': 'ok', 'similarity': 1., 'exact': True, 'abstention': False, 'valid_sequence_output': True,
                   'invalid_output': False, 'error': ''}
            row = {'model': self.model, 'method_group': 'academic'}
            row.update({'pdf_' + k: v for k, v in pdf.items()})
            row.update({'name_only_' + k: v for k, v in name.items()})
            row.update(paired=name['similarity'] is not None,
                       pdf_minus_name_only=1.-name['similarity'] if name['similarity'] is not None else None)
            rows.append(row)
        s = analysis.summarize(rows, resamples=1000)[0]
        self.assertEqual((s['scheduled_n'], s['paired_n'], s['name_only_scored_n']), (6, 2, 2))
        self.assertEqual(s['name_only_abstentions'], 1)
        self.assertEqual(s['name_only_invalid_outputs'], 1)
        self.assertEqual(s['name_only_technical_failures'], 1)
        self.assertEqual(s['name_only_exact_attempt_rate'], 0.)
        self.assertEqual((s['mean_pdf_minus_name_only'], s['ci95_low'], s['ci95_high']), (1., 1., 1.))
        self.assertEqual((s['pdf_wins'], s['name_only_wins'], s['ties']), (2, 0, 0))

    def test_pair_bootstrap_direction_and_ties(self):
        rows = []
        for delta in (.5, -.5, 0.):
            r = {'model': self.model, 'method_group': 'commercial', 'paired': True, 'pdf_minus_name_only': delta}
            for p in ('pdf', 'name_only'):
                r.update({p+'_similarity': .5 + delta if p == 'pdf' else .5,
                          p+'_status': 'ok', p+'_valid_sequence_output': True, p+'_exact': False,
                          p+'_abstention': False, p+'_invalid_output': False})
            rows.append(r)
        s = analysis.summarize(rows, resamples=10000)[0]
        self.assertEqual(s['mean_pdf_minus_name_only'], 0.)
        self.assertEqual((s['pdf_wins'], s['name_only_wins'], s['ties']), (1, 1, 1))
        self.assertEqual((s['ci95_low'], s['ci95_high']), (-.5, .5))

    def vertex_response(self):
        return {'modelVersion': run.VERTEX_MODEL, 'candidates': [{
            'finishReason': 'STOP', 'content': {'parts': [{'text': '{"libraries": []}'}]}}]}

    def test_vertex_same_text_no_tools_high_budget_and_response_checks(self):
        original = run.payload(self.protocol, run.GEMINI)
        vertex = run.payload(self.protocol, run.GEMINI, 'vertex')
        self.assertEqual(vertex['contents'], [{'role': 'user', 'parts': [{'text': run.prompt(self.protocol)}]}])
        self.assertEqual(vertex['systemInstruction']['parts'][0]['text'], original['messages'][0]['content'])
        self.assertEqual(vertex['tools'], [])
        self.assertEqual(vertex['generationConfig']['maxOutputTokens'], 64000)
        self.assertEqual(vertex['generationConfig']['thinkingConfig']['thinkingLevel'], 'HIGH')
        for key in ('cachedContent', 'fileData', 'inlineData'):
            self.assertNotIn(key, json.dumps(vertex))
        examples = [(self.vertex_response(), 'completed')]
        wrong = self.vertex_response(); wrong['modelVersion'] = 'different'
        examples.append((wrong, 'condition_violation'))
        for key in ('groundingMetadata', 'urlContextMetadata'):
            value = self.vertex_response(); value['candidates'][0][key] = {'source': 'web'}
            examples.append((value, 'condition_violation'))
        tool = self.vertex_response(); tool['candidates'][0]['content']['parts'].append({'functionCall': {'name': 'search'}})
        examples.append((tool, 'condition_violation'))
        truncated = self.vertex_response(); truncated['candidates'][0]['finishReason'] = 'MAX_TOKENS'
        examples.append((truncated, 'invalid_output'))
        examples.append(({'error': {'code': 429}}, 'technical_failure'))
        for response, expected in examples:
            self.assertEqual(run.interpret_vertex(response, run.GEMINI, 'test')[0]['status'], expected)

    def test_vertex_amendment_preserves_openrouter_attempt_and_mixed_analysis(self):
        project = 'gen-lang-client-0325887617'
        with tempfile.TemporaryDirectory() as temp, patch('builtins.print'):
            out = Path(temp)
            legacy = run.experiment_lock(self.config)
            legacy['implementation_sha256']['runner'] = run.LEGACY_RUNNER_SHA256
            run.write(out / 'experiment.json', legacy)
            response = self.response(); response['model'] = run.GEMINI
            with patch.object(run.api, '_post_json', return_value=response):
                run.run_one(out, self.protocol, run.GEMINI, 'placeholder')
            before = {p: p.read_bytes() for p in out.glob('*/*/*') if p.is_file()}
            root = run.prepare_directory(out, self.config, project)
            self.assertEqual(root['amendments'][0]['previous_experiment'], legacy)
            self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
            with patch.object(run, 'post_vertex', return_value=self.vertex_response()) as post:
                self.assertEqual(run.run_one(out, self.protocol, run.GEMINI, '', vertex_project=project)['status'],
                                 'skipped_existing_attempt')
                post.assert_not_called()
                run.run_one(out, self.config['protocols'][1], run.GEMINI, '', vertex_project=project, sandboxed=True)
                self.assertEqual(post.call_count, 1)
            variants = {
                p['protocol_id']: [{'document': {'protocol_id': p['protocol_id'], 'library_sequence': 'ACGT'},
                                    'length_choices': []}]
                for p in self.config['protocols']
            }
            baseline = {(model, pid): {'similarity': 1.0} for model in run.MODELS for pid in variants}
            with patch.object(analysis, 'references', return_value=(variants, {'snapshot': 'test'})), \
                    patch.object(analysis, 'pdf_baseline', return_value=(baseline, dict.fromkeys(baseline, {}))), \
                    patch.object(analysis, 'OUT', run.ROOT / 'paper/methods'):
                rows, provenance = analysis.collect(out, self.config, allow_incomplete=True)
            gemini = [r for r in rows if r['model'] == run.GEMINI]
            self.assertEqual(gemini[0]['name_only_provider'], 'openrouter')
            self.assertEqual(gemini[1]['name_only_provider'], 'vertex')
            self.assertTrue(gemini[1]['name_only_sandboxed'])
            self.assertEqual(len(provenance['transport_amendments']), 1)
            self.assertIn('provider/API routes differ', analysis.transport_note(rows))
            removed = next(iter(before))
            removed.unlink()
            with self.assertRaisesRegex(ValueError, 'artifact missing.*explicit run restart'):
                run.validate_experiment(out, self.config)
            removed.write_bytes(before[removed])
            next(iter(before)).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Preserved'):
                run.validate_experiment(out, self.config)

    def test_vertex_only_does_not_need_openrouter_key(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {}, clear=True), \
                patch.object(run, 'adc_file', return_value=Path('/fake/adc.json')), \
                patch.object(run, 'post_vertex', return_value=self.vertex_response()) as post, \
                patch.object(run.api, '_post_json') as openrouter, patch('builtins.print'):
            self.assertEqual(run.main(['--protocols', str(self.config_path), '--out', temp, '--model', run.GEMINI, '--gemini-provider', 'vertex',
                                       '--gcp-project', 'gen-lang-client-0325887617']), 0)
            self.assertEqual(post.call_count, 20)
            openrouter.assert_not_called()

    def test_adc_refresh_and_vertex_headers_keep_credentials_out_of_model_payload(self):
        project = 'gen-lang-client-0325887617'
        with tempfile.TemporaryDirectory() as temp:
            credential_path = Path(temp) / 'adc.json'
            run.write(credential_path, {'type': 'authorized_user', 'quota_project_id': project,
                                       'client_id': 'test-client', 'client_secret': 'test-secret',
                                       'refresh_token': 'test-refresh'})
            request = run.payload(self.protocol, run.GEMINI, 'vertex')
            with patch.dict('os.environ', {'GOOGLE_APPLICATION_CREDENTIALS': str(credential_path)}), \
                    patch.object(run.api.urllib.request, 'urlopen') as post:
                post.return_value.__enter__.return_value.read.side_effect = [
                    b'{"access_token": "test-access"}', json.dumps(self.vertex_response()).encode()]
                response = run.post_vertex(project, request)
            self.assertEqual(response['modelVersion'], run.VERTEX_MODEL)
            oauth, generation = [call.args[0] for call in post.call_args_list]
            self.assertEqual(oauth.full_url, 'https://oauth2.googleapis.com/token')
            self.assertEqual(generation.full_url, run.vertex_url(project))
            self.assertEqual(generation.get_header('Authorization'), 'Bearer test-access')
            self.assertEqual(generation.get_header('X-goog-user-project'), project)
            self.assertEqual(json.loads(generation.data), request)
            for secret in ('test-access', 'test-refresh', 'test-secret', 'test-client'):
                self.assertNotIn(secret, generation.data.decode())

    def test_docker_context_and_egress_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            hashes = sandbox.context(directory, run.DEFAULT_CONFIG)
            self.assertEqual(len(list(directory.rglob('*.pdf'))), 0)
            self.assertFalse(any('groundtruth' in name for name in hashes))
            self.assertFalse((directory / 'runs').exists())
            self.assertFalse((directory / '.env').exists())
            out = directory / 'output'
            out.mkdir()
            config = sandbox.compose_spec(directory, out, [run.GEMINI], 'gen-lang-client-0325887617', False,
                                          credentials=directory / 'adc.json')
            runner = config['services']['runner']
            self.assertEqual(runner['networks'], ['isolated'])
            self.assertTrue(config['networks']['isolated']['internal'])
            self.assertEqual(runner['cap_drop'], ['ALL'])
            self.assertTrue(runner['read_only'])
            self.assertNotIn('OPENROUTER_API_KEY', runner['environment'])
            self.assertEqual([m['target'] for m in runner['volumes']], ['/runs', '/credentials/adc.json'])
            self.assertTrue(runner['volumes'][1]['read_only'])
            self.assertNotIn('docker.sock', json.dumps(config))
            policy = run.read(directory / 'policy.json')
            self.assertEqual(policy, {'provider_hosts': ['aiplatform.googleapis.com', 'oauth2.googleapis.com'], 'setup_hosts': []})
            self.assertEqual(config['services']['provider-egress']['networks'], ['isolated', 'egress'])


if __name__ == '__main__':
    unittest.main()
