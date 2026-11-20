---
name: fetch-protocol-bundle
description: Collect the original paper, protocol or vendor manual, and relevant supplementary files for one sequencing protocol into a local folder. Use with a protocol name, paper title, DOI, or source link.
---

# Fetch a protocol bundle

Start with the supplied name or link and the destination directory. Deliver the original documents and a per-file source manifest. This skill can run from any workspace with web and local file access.

## Identify the protocol

Resolve the method name, variant, and version. Follow the DOI or publisher record when starting from a paper. For a commercial kit, check the product name, chemistry generation, manual number, and revision on the vendor's document. If the starting point matches multiple versions and the choice changes the files, show the candidates before collecting.

## Select the documents

- **Academic method.** Find the original method paper, its supplementary-material index, and any linked author protocol. Consult the publisher, an open full-text repository, or the authors' protocol page.
- **Commercial kit.** Collect the manual for the requested generation and any applicable companion protocols. Identify a platform-family paper as a `foundational_paper` and record which version the manual actually covers.
- **Attachments.** Follow the paper's supplement descriptions and methods citations. Look for supplementary methods, oligo or barcode tables, library diagrams, and protocol-specific technical notes. Keep Word documents and Excel workbooks in their original formats, including sheets or appendices inside a combined supplement. Leave out standalone figure-source data and downstream analysis matrices unless they contain protocol evidence.

## Save and verify

1. Store original files in `<destination>/<protocol_id>/`. Use an existing protocol ID when available. Preserve inventory filenames when matching a benchmark. Open each PDF, check that a Word document or workbook opens as its claimed type, and confirm its title or attachment label. Login pages sometimes download with a PDF filename.
2. Compute byte count and SHA-256 for each original. Reuse identical files. If a publisher serves different bytes at the same URL, preserve both versions with distinct paths and note the difference.
3. Write a tab-separated `SOURCE_MANIFEST.tsv` in the protocol folder. Use the collection's column order below, with `local_file` relative to the destination (including the protocol folder). Record the paper or vendor landing page separately from the attachment URL. Leave unknown values blank. Use `notes` for revision, applicability, or a known license.

   ```text
   folder	local_file	kind	title	doi	landing_url	direct_url	bytes	sha256	notes
   ```

4. Keep earlier manifest entries when extending a bundle. Record an inaccessible or missing attachment with its identifying link, blank file/hash fields, and a reason in `notes`. Keep third-party documents local unless their redistribution terms allow sharing.

Report the folder, manifest, exact version identified, files collected, and any gaps. The bundle is complete enough to hand off when another reader can trace each file to its source and distinguish version-specific evidence from background.
