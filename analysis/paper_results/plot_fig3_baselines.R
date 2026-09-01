#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(dplyr)
  library(ggplot2)
  library(pheatmap)
  library(scales)
  library(tidyr)
})

# Figure 3 uses the complete 20-protocol native-CLI baseline in Panel A and the
# ten-protocol cross-system intersection in Panels B-C.
#
# Panel C reports protocol-level prevalence, not event count: a protocol
# contributes at most one observation to a category. This keeps the scale in
# [0, 1] and prevents several deterministic manifestations of one failure from
# inflating a system's profile. Until scientific validity and attribution have
# been adjudicated, these values are explicitly labelled as provisional output
# discrepancies rather than agent root errors.

repo_root <- normalizePath(".", mustWork = TRUE)
data_root <- file.path(repo_root, "analysis", "paper_results")
figure_root <- file.path(data_root, "figures")
dir.create(figure_root, recursive = TRUE, showWarnings = FALSE)

trials <- read.csv(
  file.path(data_root, "fig3_trials_extracted.csv"),
  na.strings = c("", "NA"),
  check.names = FALSE,
  stringsAsFactors = FALSE
)
native_trials <- read.csv(
  file.path(data_root, "fig3_native_trials_extracted.csv"),
  na.strings = c("", "NA"),
  check.names = FALSE,
  stringsAsFactors = FALSE
)
inventory <- read.csv(
  file.path(data_root, "fig3_system_inventory.csv"),
  na.strings = c("", "NA"),
  check.names = FALSE,
  stringsAsFactors = FALSE
)
errors <- read.csv(
  file.path(data_root, "fig3_error_observations_extracted.csv"),
  na.strings = c("", "NA"),
  check.names = FALSE,
  stringsAsFactors = FALSE
)

expected_protocols <- 10L
expected_native_protocols <- 20L
expected_benchmark_version <- "4.5.0"
bootstrap_replicates <- 5000L

observed_versions <- sort(unique(na.omit(trials$benchmark_version)))
native_observed_versions <- sort(unique(na.omit(native_trials$benchmark_version)))
if (!identical(observed_versions, expected_benchmark_version) ||
    !identical(native_observed_versions, expected_benchmark_version)) {
  stop(sprintf(
    paste0(
      "Figure 3 inputs must use benchmark %s; common/native versions are ",
      "%s / %s."
    ),
    expected_benchmark_version,
    paste(observed_versions, collapse = ";"),
    paste(native_observed_versions, collapse = ";")
  ))
}

protocol_key <- trials %>%
  distinct(protocol_id, protocol_label, protocol_order) %>%
  arrange(protocol_order)
if (nrow(protocol_key) != expected_protocols) {
  stop(sprintf(
    "Expected %d common protocols but found %d.",
    expected_protocols, nrow(protocol_key)
  ))
}
protocol_ids_order <- protocol_key$protocol_id
protocol_labels <- setNames(protocol_key$protocol_label, protocol_key$protocol_id)

system_checks_observed <- trials %>%
  group_by(
    system_id, system_label, model, harness, system_group, display_order
  ) %>%
  summarise(
    protocols_scheduled = n(),
    result_files = sum(result_present == 1, na.rm = TRUE),
    valid_completions = sum(valid_completion == 1, na.rm = TRUE),
    missing_t2 = sum(is.na(t2_required_family_f1)),
    missing_t3_transition = sum(is.na(t3_molecular_transition_f1)),
    missing_t3_state = sum(is.na(t3_state_f1)),
    missing_typed_edge = sum(is.na(t3_typed_edge_f1)),
    completed_error_reviews = sum(
      error_review_status %in% c("complete", "not_required"), na.rm = TRUE
    ),
    .groups = "drop"
  )

native_system_checks_observed <- native_trials %>%
  group_by(system_id) %>%
  summarise(
    native_protocols_scheduled = n(),
    native_result_files = sum(result_present == 1, na.rm = TRUE),
    native_valid_completions = sum(valid_completion == 1, na.rm = TRUE),
    native_missing_t2 = sum(is.na(primary_t2_required_family_f1)),
    native_missing_t3_transition = sum(
      is.na(primary_t3_molecular_transition_f1)
    ),
    native_missing_t3_state = sum(is.na(primary_t3_state_f1)),
    native_missing_typed_edge = sum(is.na(primary_t3_typed_edge_f1)),
    .groups = "drop"
  )

system_checks <- inventory %>%
  select(
    system_id, system_label, model, harness, system_group, display_order,
    run_available, exclusion_reason
  ) %>%
  left_join(
    system_checks_observed,
    by = c(
      "system_id", "system_label", "model", "harness", "system_group",
      "display_order"
    )
  ) %>%
  left_join(native_system_checks_observed, by = "system_id") %>%
  mutate(
    panel_a_status = case_when(
      system_group != "native" ~ "not applicable: shared harness",
      run_available != 1 ~ "excluded: no preserved run",
      native_protocols_scheduled != expected_native_protocols ~
        "excluded: incomplete native protocol panel",
      native_missing_t2 + native_missing_t3_transition +
          native_missing_t3_state + native_missing_typed_edge > 0 ~
        "excluded: missing native primary metric",
      TRUE ~ "included"
    ),
    panel_b_status = case_when(
      run_available != 1 ~ "excluded: no preserved run",
      protocols_scheduled != expected_protocols ~
        "excluded: incomplete protocol panel",
      missing_t3_transition > 0 ~ "excluded: missing T3 transition F1",
      TRUE ~ "included"
    )
  )

panel_b_ids <- system_checks %>%
  filter(panel_b_status == "included") %>%
  arrange(display_order) %>%
  pull(system_id)
native_ids <- system_checks %>%
  filter(panel_a_status == "included") %>%
  arrange(display_order) %>%
  pull(system_id)

system_label_table <- system_checks %>%
  select(system_id, system_label)
system_labels <- setNames(
  system_label_table$system_label, system_label_table$system_id
)

# Cluster every available system from its ten-protocol T3 transition-F1 vector.
# Correlation distance emphasizes relative protocol-specific strengths and
# weaknesses; average linkage provides the displayed row dendrogram.
cluster_wide <- trials %>%
  filter(system_id %in% panel_b_ids) %>%
  select(system_id, protocol_id, t3_molecular_transition_f1) %>%
  mutate(protocol_id = factor(protocol_id, levels = protocol_ids_order)) %>%
  pivot_wider(
    names_from = protocol_id,
    values_from = t3_molecular_transition_f1
  )
cluster_wide <- cluster_wide[
  match(panel_b_ids, cluster_wide$system_id),
  c("system_id", protocol_ids_order),
  drop = FALSE
]
cluster_matrix <- as.matrix(
  cluster_wide[, protocol_ids_order, drop = FALSE]
)
rownames(cluster_matrix) <- cluster_wide$system_id

correlation <- cor(t(cluster_matrix), method = "pearson", use = "everything")
if (anyNA(correlation)) {
  stop("Cannot cluster systems: correlation distance contains NA.")
}
correlation[correlation > 1] <- 1
correlation[correlation < -1] <- -1
system_cluster <- hclust(as.dist(1 - correlation), method = "average")
system_order <- system_cluster$labels[system_cluster$order]

# Panel B omits the sole Kimi system at the author's request. Recluster the
# remaining systems so the displayed dendrogram contains only plotted rows;
# Panel C retains the full comparison set and clusters its error-rate profiles
# independently below.
panel_b_plot_ids <- setdiff(panel_b_ids, "kimi_code")
panel_b_cluster_matrix <- cluster_matrix[
  panel_b_plot_ids, protocol_ids_order, drop = FALSE
]
panel_b_correlation <- cor(
  t(panel_b_cluster_matrix), method = "pearson", use = "everything"
)
if (anyNA(panel_b_correlation)) {
  stop("Cannot cluster Panel B systems: correlation distance contains NA.")
}
panel_b_correlation[panel_b_correlation > 1] <- 1
panel_b_correlation[panel_b_correlation < -1] <- -1
panel_b_system_cluster <- hclust(
  as.dist(1 - panel_b_correlation), method = "average"
)
panel_b_system_order <- panel_b_system_cluster$labels[
  panel_b_system_cluster$order
]

order_data <- bind_rows(
  protocol_key %>%
    transmute(
      order_type = "protocol",
      position = protocol_order,
      id = protocol_id,
      label = protocol_label,
      group = NA_character_,
      status = "plotted"
    ),
  tibble(
    order_type = "system",
    position = seq_along(system_order),
    id = system_order,
    label = unname(system_labels[system_order]),
    group = system_checks$system_group[
      match(system_order, system_checks$system_id)
    ],
    status = "plotted"
  ),
  tibble(
    order_type = "system_panel_b",
    position = seq_along(panel_b_system_order),
    id = panel_b_system_order,
    label = unname(system_labels[panel_b_system_order]),
    group = system_checks$system_group[
      match(panel_b_system_order, system_checks$system_id)
    ],
    status = "plotted"
  ),
  tibble(
    order_type = "system_panel_b",
    position = NA_integer_,
    id = "kimi_code",
    label = unname(system_labels["kimi_code"]),
    group = "native",
    status = "excluded by figure design"
  ),
  system_checks %>%
    filter(panel_b_status != "included") %>%
    transmute(
      order_type = "system",
      position = NA_integer_,
      id = system_id,
      label = system_label,
      group = system_group,
      status = panel_b_status
    )
)
write.csv(
  order_data,
  file.path(data_root, "fig3_orders.csv"),
  row.names = FALSE,
  na = ""
)

heatmap_colours <- colorRampPalette(
  c("#f7fbff", "#deebf7", "#b7d4e8", "#7eafd0", "#4682b4")
)(100)
panel_b_heatmap_colours <- colorRampPalette(
  c(
    "#ffffd9", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0",
    "#225ea8", "#0c2c84"
  )
)(100)
# Panel B values occupy approximately 0.4--1.0. Mapping that observed range
# across the full palette makes protocol-level differences visually legible.
panel_b_fill_limits <- c(0.38, 1.00)
panel_c_heatmap_colours <- colorRampPalette(
  c(
    "#fcfdbf", "#feca8d", "#fd9668", "#e85362", "#bd3786",
    "#7e2482"
  )
)(100)

save_ggplot_pdf <- function(plot, filename, width, height) {
  grDevices::pdf(
    file = file.path(figure_root, filename),
    width = width,
    height = height,
    family = "Helvetica",
    paper = "special",
    useDingbats = FALSE,
    bg = "white"
  )
  print(plot)
  grDevices::dev.off()
}

# Panel A: native CLI metrics -----------------------------------------------
metric_key <- tibble(
  metric_column = c(
    "primary_t2_required_family_f1",
    "primary_t3_molecular_transition_f1",
    "primary_t3_state_f1",
    "primary_t3_typed_edge_f1"
  ),
  metric_id = c("t2_family", "t3_transition", "t3_state", "t3_edge"),
  metric_order = 1:4,
  metric_label = c(
    "Task 2 oligo-family F1",
    "Task 3 transition F1",
    "Task 3 state F1",
    "Task 3 typed-edge F1"
  )
)

# Frozen model identifiers and reasoning settings are taken from the preserved
# native-CLI result configs; Gemini's High setting is encoded in its model ID.
# The three-line labels retain the model, effort, and harness while supporting
# publication-sized axis text.
native_model_key <- tibble(
  system_id = c(
    "gpt_codex", "claude_code", "gemini_antigravity", "kimi_code"
  ),
  model_id = c(
    "gpt-5.6-sol",
    "claude-opus-5",
    "google/gemini-3.7-flash-high",
    "kimi/kimi-k3"
  ),
  model_version_label = c(
    "GPT-5.6 Sol", "Claude Opus 5", "Gemini 3.7 Flash", "Kimi K3"
  ),
  reasoning_effort = c("Max", "Max", "High", "Max"),
  native_axis_label = c(
    "GPT-5.6 Sol\nMax\nCodex",
    "Claude Opus 5\nMax\nClaude Code",
    "Gemini 3.7 Flash\nHigh\nAntigravity CLI",
    "Kimi K3\nMax\nKimi Code"
  )
)
if (!setequal(native_ids, native_model_key$system_id)) {
  stop("Native model-version labels do not match the included native systems.")
}

panel_a_protocol <- native_trials %>%
  # Panel A summarizes valid completions. The only excluded native attempt is
  # GPT/Codex on 10x 3' Feature Barcoding; it remains in the raw native CSV.
  filter(system_id %in% native_ids, valid_completion == 1) %>%
  select(
    system_id, system_label, model, harness, display_order,
    protocol_id, protocol_label, protocol_order, status, valid_completion,
    all_of(metric_key$metric_column)
  ) %>%
  pivot_longer(
    cols = all_of(metric_key$metric_column),
    names_to = "metric_column",
    values_to = "score"
  ) %>%
  left_join(metric_key, by = "metric_column") %>%
  left_join(native_model_key, by = "system_id") %>%
  arrange(display_order, metric_order, protocol_order)
write.csv(
  panel_a_protocol,
  file.path(data_root, "fig3a_native_metrics_protocols.csv"),
  row.names = FALSE,
  na = ""
)

bootstrap_ci <- function(values) {
  if (length(values) == 0L || any(is.na(values))) {
    return(c(NA_real_, NA_real_))
  }
  samples <- replicate(
    bootstrap_replicates,
    mean(sample(values, length(values), replace = TRUE))
  )
  as.numeric(quantile(samples, c(0.025, 0.975), names = FALSE))
}

set.seed(20260823)
panel_a_summary <- panel_a_protocol %>%
  group_by(
    system_id, system_label, model, harness, display_order,
    model_id, model_version_label, reasoning_effort, native_axis_label,
    metric_id, metric_order, metric_label
  ) %>%
  group_modify(function(.x, .y) {
    interval <- bootstrap_ci(.x$score)
    tibble(
      protocols = nrow(.x),
      mean_score = mean(.x$score),
      ci_low = interval[[1]],
      ci_high = interval[[2]],
      bootstrap_replicates = bootstrap_replicates
    )
  }) %>%
  ungroup() %>%
  mutate(
    mean_label = sprintf("%.2f", mean_score),
    label_stagger = c(0, 0.015, 0, 0.025)[metric_order],
    label_y = pmin(ci_high + 0.04 + label_stagger, 1.08)
  ) %>%
  arrange(display_order, metric_order)
write.csv(
  panel_a_summary,
  file.path(data_root, "fig3a_native_metrics.csv"),
  row.names = FALSE,
  na = ""
)

native_labels <- system_checks %>%
  filter(system_id %in% native_ids) %>%
  arrange(display_order) %>%
  pull(system_label)
native_axis_labels <- setNames(
  native_model_key$native_axis_label,
  system_labels[native_model_key$system_id]
)
metric_colours <- c(
  "Task 2 oligo-family F1" = "#56b4e9",
  "Task 3 transition F1" = "#0072b2",
  "Task 3 state F1" = "#e69f00",
  "Task 3 typed-edge F1" = "#009e73"
)

panel_a <- panel_a_summary %>%
  mutate(
    system = factor(system_label, levels = native_labels),
    metric = factor(metric_label, levels = metric_key$metric_label)
  ) %>%
  ggplot(aes(x = system, y = mean_score, fill = metric)) +
  geom_col(
    position = position_dodge(width = 0.78),
    width = 0.68,
    colour = "white",
    linewidth = 0.25
  ) +
  geom_errorbar(
    aes(ymin = ci_low, ymax = ci_high),
    position = position_dodge(width = 0.78),
    width = 0.15,
    linewidth = 0.42,
    colour = "#333333"
  ) +
  geom_text(
    aes(y = label_y, label = mean_label),
    position = position_dodge(width = 0.78),
    vjust = 0,
    size = 3.8,
    colour = "#222222"
  ) +
  scale_fill_manual(values = metric_colours, name = NULL) +
  scale_x_discrete(labels = native_axis_labels) +
  scale_y_continuous(
    limits = c(0, 1.10),
    breaks = seq(0, 1, 0.2),
    labels = number_format(accuracy = 0.1),
    expand = expansion(mult = c(0, 0))
  ) +
  labs(
    title = "Native CLI baseline performance",
    x = NULL,
    y = "Mean score"
  ) +
  theme_classic(base_size = 13.5, base_family = "Helvetica") +
  theme(
    axis.title.y = element_text(size = 16),
    axis.text = element_text(size = 12.8, colour = "#222222"),
    axis.text.x = element_text(size = 11.2, lineheight = 0.96),
    plot.title = element_text(size = 17.5, face = "plain", colour = "#222222"),
    legend.position = "bottom",
    legend.text = element_text(size = 9.8),
    legend.key.width = grid::unit(2.0, "mm"),
    legend.spacing.x = grid::unit(0.2, "mm"),
    plot.margin = margin(6, 7, 3, 6)
  ) +
  guides(fill = guide_legend(nrow = 1, byrow = TRUE))

# Panel B: clustered T3 transition heatmap ---------------------------------
panel_b_data <- trials %>%
  filter(system_id %in% panel_b_system_order) %>%
  transmute(
    system_id,
    system_label,
    model,
    harness,
    model_annotation = model,
    harness_annotation = case_when(
      harness == "Pi" ~ "Pi",
      harness == "mini-SWE" ~ "mini-SWE",
      system_id == "gpt_codex" ~ "Codex",
      system_id == "claude_code" ~ "Claude Code",
      system_id == "gemini_antigravity" ~ "Antigravity CLI",
      system_id == "kimi_code" ~ "Kimi Code",
      TRUE ~ NA_character_
    ),
    system_group,
    clustered_system_order = match(system_id, panel_b_system_order),
    protocol_id,
    protocol_label,
    protocol_order,
    t3_molecular_transition_f1
  ) %>%
  arrange(clustered_system_order, protocol_order)
write.csv(
  panel_b_data,
  file.path(data_root, "fig3b_t3_heatmap.csv"),
  row.names = FALSE,
  na = ""
)

panel_b_matrix <- panel_b_cluster_matrix
rownames(panel_b_matrix) <- unname(system_labels[rownames(panel_b_matrix)])
colnames(panel_b_matrix) <- unname(protocol_labels[colnames(panel_b_matrix)])
panel_b_numbers <- matrix(
  ifelse(is.na(panel_b_matrix), "", sprintf("%.2f", panel_b_matrix)),
  nrow = nrow(panel_b_matrix),
  ncol = ncol(panel_b_matrix),
  dimnames = dimnames(panel_b_matrix)
)

# The clustering object uses system IDs, whereas the rendered matrix uses
# readable labels. Relabeling leaves preserves the exact clustering and order.
panel_b_cluster <- panel_b_system_cluster
panel_b_cluster$labels <- unname(system_labels[panel_b_cluster$labels])

panel_b_annotation <- panel_b_data %>%
  distinct(system_label, model_annotation, harness_annotation) %>%
  transmute(
    system_label,
    Model = factor(
      model_annotation, levels = c("GPT", "Claude", "Gemini")
    ),
    Harness = factor(
      harness_annotation,
      levels = c(
        "Pi", "mini-SWE", "Codex", "Claude Code", "Antigravity CLI"
      )
    )
  ) %>%
  as.data.frame()
rownames(panel_b_annotation) <- panel_b_annotation$system_label
panel_b_annotation$system_label <- NULL
panel_b_annotation <- panel_b_annotation[
  rownames(panel_b_matrix), , drop = FALSE
]

panel_b_annotation_colours <- list(
  Model = c(
    GPT = "#0072B2",
    Claude = "#D55E00",
    Gemini = "#009E73"
  ),
  Harness = c(
    Pi = "#66C2A5",
    `mini-SWE` = "#E6AB02",
    Codex = "#A6761D",
    `Claude Code` = "#E7298A",
    `Antigravity CLI` = "#7570B3"
  )
)

# Panel C: output-discrepancy prevalence ------------------------------------
valid_review_status <- trials %>%
  filter(system_id %in% system_order, valid_completion == 1) %>%
  group_by(system_id) %>%
  summarise(
    valid_protocols = n(),
    review_complete = all(
      error_review_status %in% c("complete", "not_required")
    ),
    .groups = "drop"
  )

category_key <- tibble(
  category = c(
    "missing_recoverable_information",
    "unsupported_completion",
    "operation_error",
    "strand_or_orientation_error",
    "molecular_state_or_assembly_error",
    "workflow_or_topology_error"
  ),
  category_order = 1:6,
  category_label = c(
    "Missing recoverable information",
    "Unsupported addition",
    "Operation / target mismatch",
    "Strand / orientation error",
    "Molecular-state assembly",
    "Workflow topology / disposition"
  ),
  category_axis_label = c(
    "Missing recoverable\ninformation",
    "Unsupported\naddition",
    "Operation / target\nmismatch",
    "Strand / orientation\nerror",
    "Molecular-state\nassembly",
    "Workflow topology /\ndisposition"
  )
)

valid_protocol_keys <- trials %>%
  filter(system_id %in% system_order, valid_completion == 1) %>%
  select(system_id, protocol_id)

substantive_errors <- errors %>%
  mutate(substantive_flag = tolower(as.character(substantive)) == "true") %>%
  inner_join(valid_protocol_keys, by = c("system_id", "protocol_id")) %>%
  filter(substantive_flag, category %in% category_key$category)

strict_panel_ready <-
  nrow(valid_review_status) == length(system_order) &&
  all(valid_review_status$review_complete)

if (strict_panel_ready) {
  profile_events <- substantive_errors %>%
    filter(
      adjudication_status == "complete",
      benchmark_validity == "valid",
      attribution == "agent"
    )
  panel_c_mode <- "adjudicated_agent_error_rates"
  panel_c_main <- "Fraction of valid protocols with at least one agent error"
} else {
  profile_events <- substantive_errors
  panel_c_mode <- "provisional_output_discrepancy_rates"
  panel_c_main <-
    "Fraction of valid protocols with at least one output discrepancy"
}

# Binary protocol/category incidence is the fair cross-system representation:
# each valid protocol contributes at most once to each category, regardless of
# how many metric rows or deterministic signatures the discrepancy generated.
profile_presence <- profile_events %>%
  distinct(system_id, protocol_id, category)

panel_c_data <- crossing(
  system_id = system_order,
  category = category_key$category
) %>%
  left_join(
    profile_presence %>%
      count(system_id, category, name = "protocols_with_discrepancy"),
    by = c("system_id", "category")
  ) %>%
  left_join(valid_review_status, by = "system_id") %>%
  left_join(category_key, by = "category") %>%
  mutate(
    system_label = unname(system_labels[system_id]),
    system_group = system_checks$system_group[
      match(system_id, system_checks$system_id)
    ],
    clustered_system_order = match(system_id, system_order),
    protocols_with_discrepancy = replace_na(
      protocols_with_discrepancy, 0L
    ),
    prevalence = protocols_with_discrepancy / valid_protocols,
    analysis_mode = panel_c_mode,
    benchmark_validity_status = ifelse(
      strict_panel_ready, "valid", "unresolved"
    ),
    attribution_status = ifelse(
      strict_panel_ready, "agent", "unresolved"
    ),
    data_status = ifelse(
      strict_panel_ready,
      "adjudicated benchmark-valid agent-attributed protocol rates",
      "provisional unadjudicated output-discrepancy protocol rates"
    ),
    category_factor = factor(category, levels = category_key$category),
    cell_label = sprintf("%.2f", prevalence)
  ) %>%
  arrange(clustered_system_order, category_order)

# Panel C uses the same clustered heatmap design as Panel B. Clustering is
# performed on normalized six-category protocol-incidence vectors, not on
# duplicated event rows.
panel_c_wide <- panel_c_data %>%
  select(system_id, category, prevalence) %>%
  mutate(category = factor(category, levels = category_key$category)) %>%
  pivot_wider(
    names_from = category,
    values_from = prevalence
  )
panel_c_wide <- panel_c_wide[
  match(system_order, panel_c_wide$system_id),
  c("system_id", category_key$category),
  drop = FALSE
]
panel_c_rate_matrix <- as.matrix(
  panel_c_wide[, category_key$category, drop = FALSE]
)
storage.mode(panel_c_rate_matrix) <- "numeric"
rownames(panel_c_rate_matrix) <- panel_c_wide$system_id

panel_c_correlation <- cor(
  t(panel_c_rate_matrix), method = "pearson", use = "everything"
)
if (anyNA(panel_c_correlation)) {
  stop("Cannot cluster Panel C systems: correlation distance contains NA.")
}
panel_c_correlation[panel_c_correlation > 1] <- 1
panel_c_correlation[panel_c_correlation < -1] <- -1
panel_c_system_cluster <- hclust(
  as.dist(1 - panel_c_correlation), method = "average"
)
panel_c_system_order <- panel_c_system_cluster$labels[
  panel_c_system_cluster$order
]

panel_c_data <- panel_c_data %>%
  mutate(
    clustered_system_order = match(system_id, panel_c_system_order)
  ) %>%
  arrange(clustered_system_order, category_order)
write.csv(
  panel_c_data,
  file.path(data_root, "fig3c_error_profiles.csv"),
  row.names = FALSE,
  na = ""
)

order_data <- bind_rows(
  order_data,
  tibble(
    order_type = "system_panel_c",
    position = seq_along(panel_c_system_order),
    id = panel_c_system_order,
    label = unname(system_labels[panel_c_system_order]),
    group = system_checks$system_group[
      match(panel_c_system_order, system_checks$system_id)
    ],
    status = "plotted"
  )
)
write.csv(
  order_data,
  file.path(data_root, "fig3_orders.csv"),
  row.names = FALSE,
  na = ""
)

panel_c_matrix <- panel_c_rate_matrix
rownames(panel_c_matrix) <- unname(system_labels[rownames(panel_c_matrix)])
colnames(panel_c_matrix) <- unname(
  category_key$category_label[
    match(colnames(panel_c_matrix), category_key$category)
  ]
)
panel_c_numbers <- matrix(
  sprintf("%.2f", as.numeric(panel_c_matrix)),
  nrow = nrow(panel_c_matrix),
  ncol = ncol(panel_c_matrix),
  dimnames = dimnames(panel_c_matrix)
)

panel_c_cluster <- panel_c_system_cluster
panel_c_cluster$labels <- unname(system_labels[panel_c_cluster$labels])

panel_c_annotation <- trials %>%
  filter(system_id %in% system_order) %>%
  distinct(system_id, system_label, model, harness) %>%
  transmute(
    system_label,
    Model = factor(model, levels = c("GPT", "Claude", "Gemini", "Kimi")),
    Harness = factor(
      case_when(
        harness == "Pi" ~ "Pi",
        harness == "mini-SWE" ~ "mini-SWE",
        system_id == "gpt_codex" ~ "Codex",
        system_id == "claude_code" ~ "Claude Code",
        system_id == "gemini_antigravity" ~ "Antigravity CLI",
        system_id == "kimi_code" ~ "Kimi Code",
        TRUE ~ NA_character_
      ),
      levels = c(
        "Pi", "mini-SWE", "Codex", "Claude Code", "Antigravity CLI",
        "Kimi Code"
      )
    )
  ) %>%
  as.data.frame()
rownames(panel_c_annotation) <- panel_c_annotation$system_label
panel_c_annotation$system_label <- NULL
panel_c_annotation <- panel_c_annotation[
  rownames(panel_c_matrix), , drop = FALSE
]

panel_c_annotation_colours <- list(
  Model = c(
    GPT = "#0072B2",
    Claude = "#D55E00",
    Gemini = "#009E73",
    Kimi = "#CC79A7"
  ),
  Harness = c(
    Pi = "#66C2A5",
    `mini-SWE` = "#E6AB02",
    Codex = "#A6761D",
    `Claude Code` = "#E7298A",
    `Antigravity CLI` = "#7570B3",
    `Kimi Code` = "#666666"
  )
)

# Protocol-centric companion to Panel C. Each cell is the fraction of valid
# systems for which a protocol has at least one discrepancy in the category.
valid_protocol_status <- trials %>%
  filter(system_id %in% system_order, valid_completion == 1) %>%
  group_by(protocol_id) %>%
  summarise(valid_systems = n(), .groups = "drop")

protocol_error_data <- crossing(
  protocol_id = protocol_ids_order,
  category = category_key$category
) %>%
  left_join(
    profile_presence %>%
      count(protocol_id, category, name = "systems_with_discrepancy"),
    by = c("protocol_id", "category")
  ) %>%
  left_join(valid_protocol_status, by = "protocol_id") %>%
  left_join(category_key, by = "category") %>%
  mutate(
    protocol_label = unname(protocol_labels[protocol_id]),
    systems_with_discrepancy = replace_na(systems_with_discrepancy, 0L),
    prevalence = systems_with_discrepancy / valid_systems,
    analysis_mode = panel_c_mode,
    data_status = ifelse(
      strict_panel_ready,
      "adjudicated benchmark-valid agent-attributed system rates",
      "provisional unadjudicated output-discrepancy system rates"
    ),
    cell_label = sprintf("%.2f", prevalence)
  )

protocol_error_wide <- protocol_error_data %>%
  select(protocol_id, category, prevalence) %>%
  mutate(category = factor(category, levels = category_key$category)) %>%
  pivot_wider(names_from = category, values_from = prevalence)
protocol_error_wide <- protocol_error_wide[
  match(protocol_ids_order, protocol_error_wide$protocol_id),
  c("protocol_id", category_key$category),
  drop = FALSE
]
protocol_error_rate_matrix <- as.matrix(
  protocol_error_wide[, category_key$category, drop = FALSE]
)
storage.mode(protocol_error_rate_matrix) <- "numeric"
rownames(protocol_error_rate_matrix) <- protocol_error_wide$protocol_id

protocol_error_correlation <- cor(
  t(protocol_error_rate_matrix), method = "pearson", use = "everything"
)
if (anyNA(protocol_error_correlation)) {
  stop("Cannot cluster protocol-centric error profiles: correlation contains NA.")
}
protocol_error_correlation[protocol_error_correlation > 1] <- 1
protocol_error_correlation[protocol_error_correlation < -1] <- -1
protocol_error_cluster <- hclust(
  as.dist(1 - protocol_error_correlation), method = "average"
)
protocol_error_order <- protocol_error_cluster$labels[
  protocol_error_cluster$order
]

protocol_error_data <- protocol_error_data %>%
  mutate(clustered_protocol_order = match(protocol_id, protocol_error_order)) %>%
  arrange(clustered_protocol_order, category_order)
write.csv(
  protocol_error_data,
  file.path(data_root, "fig3c_protocol_error_profiles.csv"),
  row.names = FALSE,
  na = ""
)

order_data <- bind_rows(
  order_data,
  tibble(
    order_type = "protocol_error_profile",
    position = seq_along(protocol_error_order),
    id = protocol_error_order,
    label = unname(protocol_labels[protocol_error_order]),
    group = NA_character_,
    status = "plotted"
  )
)
write.csv(
  order_data,
  file.path(data_root, "fig3_orders.csv"),
  row.names = FALSE,
  na = ""
)

protocol_error_matrix <- protocol_error_rate_matrix
rownames(protocol_error_matrix) <- unname(
  protocol_labels[rownames(protocol_error_matrix)]
)
colnames(protocol_error_matrix) <- unname(
  category_key$category_label[
    match(colnames(protocol_error_matrix), category_key$category)
  ]
)
protocol_error_numbers <- matrix(
  sprintf("%.2f", as.numeric(protocol_error_matrix)),
  nrow = nrow(protocol_error_matrix),
  ncol = ncol(protocol_error_matrix),
  dimnames = dimnames(protocol_error_matrix)
)
protocol_error_plot_cluster <- protocol_error_cluster
protocol_error_plot_cluster$labels <- unname(
  protocol_labels[protocol_error_plot_cluster$labels]
)
protocol_error_main <- ifelse(
  strict_panel_ready,
  "Fraction of valid systems with at least one agent error",
  "Fraction of valid systems with at least one output discrepancy"
)

# Supplementary metric heatmaps use the same systems, protocols, annotation
# scheme, and visual style as Panel B. Each metric is clustered independently
# from its ten-protocol vector and retains the absolute 0--1 score scale.
supp_metric_specs <- data.frame(
  panel_key = c("t2", "state", "edge"),
  metric_column = c(
    "t2_required_family_f1",
    "t3_state_f1",
    "t3_typed_edge_f1"
  ),
  metric_title = c(
    "Task 2 oligo-family F1",
    "Task 3 molecular-state F1",
    "Task 3 typed-edge F1"
  ),
  output_stem = c(
    "fig3b_t2_heatmap",
    "fig3b_t3_state_heatmap",
    "fig3b_t3_typed_edge_heatmap"
  ),
  stringsAsFactors = FALSE
)

supp_metric_results <- list()
supp_metric_order_rows <- list()
for (spec_index in seq_len(nrow(supp_metric_specs))) {
  spec <- supp_metric_specs[spec_index, ]
  metric_column <- spec$metric_column
  metric_data <- trials %>%
    filter(system_id %in% panel_b_plot_ids) %>%
    transmute(
      system_id,
      system_label,
      model,
      harness,
      system_group,
      protocol_id,
      protocol_label,
      protocol_order,
      score = .data[[metric_column]]
    )
  if (nrow(metric_data) != length(panel_b_plot_ids) * expected_protocols ||
      anyNA(metric_data$score)) {
    stop(sprintf(
      "Supplementary %s heatmap does not contain a complete system-protocol matrix.",
      spec$panel_key
    ))
  }

  metric_wide <- metric_data %>%
    select(system_id, protocol_id, score) %>%
    mutate(protocol_id = factor(protocol_id, levels = protocol_ids_order)) %>%
    pivot_wider(names_from = protocol_id, values_from = score)
  metric_wide <- metric_wide[
    match(panel_b_plot_ids, metric_wide$system_id),
    c("system_id", protocol_ids_order),
    drop = FALSE
  ]
  metric_matrix <- as.matrix(
    metric_wide[, protocol_ids_order, drop = FALSE]
  )
  storage.mode(metric_matrix) <- "numeric"
  rownames(metric_matrix) <- metric_wide$system_id

  metric_correlation <- cor(
    t(metric_matrix), method = "pearson", use = "everything"
  )
  if (anyNA(metric_correlation)) {
    stop(sprintf(
      "Cannot cluster supplementary %s heatmap: correlation contains NA.",
      spec$panel_key
    ))
  }
  metric_correlation[metric_correlation > 1] <- 1
  metric_correlation[metric_correlation < -1] <- -1
  metric_cluster <- hclust(
    as.dist(1 - metric_correlation), method = "average"
  )
  metric_system_order <- metric_cluster$labels[metric_cluster$order]

  metric_data <- metric_data %>%
    mutate(clustered_system_order = match(system_id, metric_system_order)) %>%
    arrange(clustered_system_order, protocol_order)
  write.csv(
    metric_data,
    file.path(data_root, paste0(spec$output_stem, ".csv")),
    row.names = FALSE,
    na = ""
  )

  display_matrix <- metric_matrix
  rownames(display_matrix) <- unname(system_labels[rownames(display_matrix)])
  colnames(display_matrix) <- unname(protocol_labels[colnames(display_matrix)])
  display_numbers <- matrix(
    sprintf("%.2f", as.numeric(display_matrix)),
    nrow = nrow(display_matrix),
    ncol = ncol(display_matrix),
    dimnames = dimnames(display_matrix)
  )
  display_cluster <- metric_cluster
  display_cluster$labels <- unname(system_labels[display_cluster$labels])
  display_annotation <- panel_b_annotation[
    rownames(display_matrix), , drop = FALSE
  ]

  supp_metric_results[[spec$panel_key]] <- list(
    matrix = display_matrix,
    numbers = display_numbers,
    cluster = display_cluster,
    annotation = display_annotation,
    title = spec$metric_title,
    output_stem = spec$output_stem
  )
  supp_metric_order_rows[[spec$panel_key]] <- tibble(
    order_type = paste0("system_", spec$panel_key, "_heatmap"),
    position = seq_along(metric_system_order),
    id = metric_system_order,
    label = unname(system_labels[metric_system_order]),
    group = system_checks$system_group[
      match(metric_system_order, system_checks$system_id)
    ],
    status = "plotted"
  )
}

order_data <- bind_rows(order_data, bind_rows(supp_metric_order_rows))
write.csv(
  order_data,
  file.path(data_root, "fig3_orders.csv"),
  row.names = FALSE,
  na = ""
)

# Write panels --------------------------------------------------------------
panels_to_write <- trimws(strsplit(
  Sys.getenv("FIG3_PANELS", unset = "a,b,c"), ",", fixed = TRUE
)[[1]])

if ("a" %in% panels_to_write) {
  save_ggplot_pdf(panel_a, "fig3a_native_metrics.pdf", 6.4, 4.65)
}
if ("b" %in% panels_to_write) {
  grDevices::pdf(
    file = file.path(figure_root, "fig3b_t3_heatmap.pdf"),
    width = 10.5,
    height = 5.25,
    family = "Helvetica",
    paper = "special",
    useDingbats = FALSE,
    bg = "white"
  )
  panel_b_heatmap <- pheatmap(
    panel_b_matrix,
    color = panel_b_heatmap_colours,
    breaks = seq(
      panel_b_fill_limits[1], panel_b_fill_limits[2],
      length.out = length(panel_b_heatmap_colours) + 1
    ),
    cluster_rows = panel_b_cluster,
    cluster_cols = FALSE,
    treeheight_row = 50,
    treeheight_col = 0,
    border_color = "white",
    display_numbers = panel_b_numbers,
    number_color = "#1f1f1f",
    fontsize = 10.5,
    fontsize_row = 12.0,
    fontsize_col = 9.6,
    fontsize_number = 9.5,
    angle_col = 45,
    annotation_row = panel_b_annotation,
    annotation_colors = panel_b_annotation_colours,
    annotation_legend = TRUE,
    annotation_names_row = FALSE,
    legend_breaks = c(0.4, 0.6, 0.8, 1.0),
    legend_labels = c("0.4", "0.6", "0.8", "1.0"),
    main = "Task 3 molecular-transition F1",
    silent = TRUE
  )

  # Enlarge the Panel B title without inheriting pheatmap's bold default.
  main_index <- which(panel_b_heatmap$gtable$layout$name == "main")
  if (length(main_index) != 1L) {
    stop("Could not identify the Panel B title grob.")
  }
  main_grob <- panel_b_heatmap$gtable$grobs[[main_index]]
  main_grob$gp <- grid::gpar(fontsize = 17.5, fontface = "plain")
  panel_b_heatmap$gtable$grobs[[main_index]] <- main_grob
  main_row <- panel_b_heatmap$gtable$layout$t[main_index]
  panel_b_heatmap$gtable$heights[main_row] <-
    1.5 * grid::grobHeight(main_grob)

  # Use white numbers on darker cells and dark numbers on lighter cells.
  matrix_index <- which(panel_b_heatmap$gtable$layout$name == "matrix")
  matrix_grob <- panel_b_heatmap$gtable$grobs[[matrix_index]]
  text_child_index <- which(vapply(
    matrix_grob$children,
    function(child) inherits(child, "text"),
    logical(1)
  ))
  if (length(text_child_index) != 1L) {
    stop("Could not identify the Panel B numeric-label grob.")
  }
  number_grob <- matrix_grob$children[[text_child_index]]
  number_values <- suppressWarnings(as.numeric(number_grob$label))
  number_grob$gp$col <- ifelse(number_values >= 0.72, "white", "#1f1f1f")
  matrix_grob$children[[text_child_index]] <- number_grob
  panel_b_heatmap$gtable$grobs[[matrix_index]] <- matrix_grob
  grid::grid.newpage()
  grid::grid.draw(panel_b_heatmap$gtable)
  grDevices::dev.off()
}
if ("c" %in% panels_to_write) {
  grDevices::pdf(
    file = file.path(figure_root, "fig3c_error_profiles.pdf"),
    width = 8.6,
    height = 5.25,
    family = "Helvetica",
    paper = "special",
    useDingbats = FALSE,
    bg = "white"
  )
  panel_c_heatmap <- pheatmap(
    panel_c_matrix,
    color = panel_c_heatmap_colours,
    breaks = seq(
      0, 1,
      length.out = length(panel_c_heatmap_colours) + 1
    ),
    cluster_rows = panel_c_cluster,
    cluster_cols = FALSE,
    treeheight_row = 50,
    treeheight_col = 0,
    border_color = "white",
    display_numbers = panel_c_numbers,
    number_color = "#1f1f1f",
    fontsize = 10.5,
    fontsize_row = 10.2,
    fontsize_col = 9.6,
    fontsize_number = 8.5,
    angle_col = 45,
    annotation_row = panel_c_annotation,
    annotation_colors = panel_c_annotation_colours,
    annotation_legend = TRUE,
    annotation_names_row = FALSE,
    legend_breaks = c(0, 0.5, 1),
    legend_labels = c("0", "0.5", "1"),
    main = panel_c_main,
    silent = TRUE
  )

  # Use white numbers on darker cells and dark numbers on lighter cells.
  matrix_index <- which(panel_c_heatmap$gtable$layout$name == "matrix")
  matrix_grob <- panel_c_heatmap$gtable$grobs[[matrix_index]]
  text_child_index <- which(vapply(
    matrix_grob$children,
    function(child) inherits(child, "text"),
    logical(1)
  ))
  if (length(text_child_index) != 1L) {
    stop("Could not identify the Panel C numeric-label grob.")
  }
  number_grob <- matrix_grob$children[[text_child_index]]
  number_values <- suppressWarnings(as.numeric(number_grob$label))
  number_grob$gp$col <- ifelse(number_values >= 0.50, "white", "#1f1f1f")
  matrix_grob$children[[text_child_index]] <- number_grob
  panel_c_heatmap$gtable$grobs[[matrix_index]] <- matrix_grob
  grid::grid.newpage()
  grid::grid.draw(panel_c_heatmap$gtable)
  grDevices::dev.off()
}
if ("p" %in% panels_to_write) {
  grDevices::pdf(
    file = file.path(figure_root, "fig3c_protocol_error_profiles.pdf"),
    width = 9.5,
    height = 5.25,
    family = "Helvetica",
    paper = "special",
    useDingbats = FALSE,
    bg = "white"
  )
  protocol_error_heatmap <- pheatmap(
    protocol_error_matrix,
    color = panel_c_heatmap_colours,
    breaks = seq(
      0, 1,
      length.out = length(panel_c_heatmap_colours) + 1
    ),
    cluster_rows = protocol_error_plot_cluster,
    cluster_cols = FALSE,
    treeheight_row = 50,
    treeheight_col = 0,
    border_color = "white",
    display_numbers = protocol_error_numbers,
    number_color = "#1f1f1f",
    fontsize = 10.5,
    fontsize_row = 10.2,
    fontsize_col = 9.6,
    fontsize_number = 8.5,
    angle_col = 45,
    legend_breaks = c(0, 0.5, 1),
    legend_labels = c("0", "0.5", "1"),
    main = protocol_error_main,
    silent = TRUE
  )

  matrix_index <- which(protocol_error_heatmap$gtable$layout$name == "matrix")
  matrix_grob <- protocol_error_heatmap$gtable$grobs[[matrix_index]]
  text_child_index <- which(vapply(
    matrix_grob$children,
    function(child) inherits(child, "text"),
    logical(1)
  ))
  if (length(text_child_index) != 1L) {
    stop("Could not identify the protocol-centric numeric-label grob.")
  }
  number_grob <- matrix_grob$children[[text_child_index]]
  number_values <- suppressWarnings(as.numeric(number_grob$label))
  number_grob$gp$col <- ifelse(number_values >= 0.50, "white", "#1f1f1f")
  matrix_grob$children[[text_child_index]] <- number_grob
  protocol_error_heatmap$gtable$grobs[[matrix_index]] <- matrix_grob
  grid::grid.newpage()
  grid::grid.draw(protocol_error_heatmap$gtable)
  grDevices::dev.off()
}
for (panel_key in intersect(
  supp_metric_specs$panel_key, panels_to_write
)) {
  metric_result <- supp_metric_results[[panel_key]]
  grDevices::pdf(
    file = file.path(
      figure_root, paste0(metric_result$output_stem, ".pdf")
    ),
    width = 11.0,
    height = 5.25,
    family = "Helvetica",
    paper = "special",
    useDingbats = FALSE,
    bg = "white"
  )
  metric_heatmap <- pheatmap(
    metric_result$matrix,
    color = panel_b_heatmap_colours,
    breaks = seq(
      0, 1, length.out = length(panel_b_heatmap_colours) + 1
    ),
    cluster_rows = metric_result$cluster,
    cluster_cols = FALSE,
    treeheight_row = 50,
    treeheight_col = 0,
    border_color = "white",
    display_numbers = metric_result$numbers,
    number_color = "#1f1f1f",
    fontsize = 10.5,
    fontsize_row = 12.0,
    fontsize_col = 9.6,
    fontsize_number = 9.5,
    angle_col = 45,
    annotation_row = metric_result$annotation,
    annotation_colors = panel_b_annotation_colours,
    annotation_legend = TRUE,
    annotation_names_row = FALSE,
    legend_breaks = c(0, 0.5, 1),
    legend_labels = c("0", "0.5", "1"),
    main = metric_result$title,
    silent = TRUE
  )

  main_index <- which(metric_heatmap$gtable$layout$name == "main")
  if (length(main_index) != 1L) {
    stop(sprintf("Could not identify the %s title grob.", panel_key))
  }
  main_grob <- metric_heatmap$gtable$grobs[[main_index]]
  main_grob$gp <- grid::gpar(fontsize = 17.5, fontface = "plain")
  metric_heatmap$gtable$grobs[[main_index]] <- main_grob
  main_row <- metric_heatmap$gtable$layout$t[main_index]
  metric_heatmap$gtable$heights[main_row] <-
    1.5 * grid::grobHeight(main_grob)

  matrix_index <- which(metric_heatmap$gtable$layout$name == "matrix")
  matrix_grob <- metric_heatmap$gtable$grobs[[matrix_index]]
  text_child_index <- which(vapply(
    matrix_grob$children,
    function(child) inherits(child, "text"),
    logical(1)
  ))
  if (length(text_child_index) != 1L) {
    stop(sprintf("Could not identify the %s numeric-label grob.", panel_key))
  }
  number_grob <- matrix_grob$children[[text_child_index]]
  number_values <- suppressWarnings(as.numeric(number_grob$label))
  number_grob$gp$col <- ifelse(number_values >= 0.68, "white", "#1f1f1f")
  matrix_grob$children[[text_child_index]] <- number_grob
  metric_heatmap$gtable$grobs[[matrix_index]] <- matrix_grob
  grid::grid.newpage()
  grid::grid.draw(metric_heatmap$gtable)
  grDevices::dev.off()
}

# Validation summary --------------------------------------------------------
system_checks <- system_checks %>%
  left_join(valid_review_status, by = "system_id") %>%
  mutate(
    panel_c_status = case_when(
      panel_b_status != "included" ~ panel_b_status,
      strict_panel_ready ~ "included: adjudicated agent-error protocol rates",
      TRUE ~ "provisional: unadjudicated output-discrepancy protocol rates"
    )
  )
write.csv(
  system_checks,
  file.path(data_root, "fig3_validation_summary.csv"),
  row.names = FALSE,
  na = ""
)

validation_lines <- c(
  "Figure 3 validation summary",
  sprintf(
    "Verifier benchmark version: %s (immutable rescore sidecars).",
    expected_benchmark_version
  ),
  sprintf(
    "Panel A: %d native systems across %d protocols; Panel B: %d systems across %d common protocols; Panel C: %d systems across the same protocols.",
    length(native_ids), expected_native_protocols,
    length(panel_b_system_order), expected_protocols, length(system_order)
  ),
  vapply(
    native_ids,
    function(system_id) {
      row <- system_checks[system_checks$system_id == system_id, ]
      scored_protocols <- unique(
        panel_a_summary$protocols[panel_a_summary$system_id == system_id]
      )
      sprintf(
        "Panel A / %s: %d scheduled protocols, %d valid completions, %d protocols included in means; missing primary Task 2/transition/state/edge = %d/%d/%d/%d.",
        row$system_label,
        row$native_protocols_scheduled,
        row$native_valid_completions,
        scored_protocols,
        row$native_missing_t2,
        row$native_missing_t3_transition,
        row$native_missing_t3_state,
        row$native_missing_typed_edge
      )
    },
    character(1)
  ),
  vapply(
    seq_len(nrow(system_checks)),
    function(i) {
      row <- system_checks[i, ]
      if (row$run_available != 1) {
        return(sprintf("%s: excluded - no preserved run.", row$system_label))
      }
      sprintf(
        "%s: %d protocols, %d valid completions; missing T2/T3/state/edge = %d/%d/%d/%d.",
        row$system_label,
        row$protocols_scheduled,
        row$valid_completions,
        row$missing_t2,
        row$missing_t3_transition,
        row$missing_t3_state,
        row$missing_typed_edge
      )
    },
    character(1)
  ),
  paste0(
    "Panel B clustering: 9 plotted systems after excluding Kimi + Kimi Code; ",
    "correlation distance (1 - Pearson r); average linkage; row dendrogram shown."
  ),
  paste0(
    "Panel C clustering: 10 plotted systems; six-category affected-protocol rate ",
    "vectors normalized by valid completions; correlation distance ",
    "(1 - Pearson r); average linkage; row ",
    "dendrogram shown."
  ),
  sprintf(
    "Panel C: mode=%s; %d unique system-protocol-category incidences; each protocol contributes at most once per category; %d/%d plotted systems have complete error review.",
    panel_c_mode,
    nrow(profile_presence),
    sum(valid_review_status$review_complete, na.rm = TRUE),
    length(system_order)
  ),
  paste0(
    "Protocol-centric companion: 10 protocols across 10 valid systems; cells are ",
    "affected-system fractions; correlation distance (1 - Pearson r); average ",
    "linkage; row dendrogram shown."
  ),
  paste0(
    "Supplementary metric heatmaps: Task 2 family, Task 3 state, and Task 3 ",
    "typed-edge F1; 9 Figure 3b systems across 10 protocols; independent ",
    "correlation-distance clustering; fixed 0-1 scales."
  ),
  "Panel D: omitted from the current figure set."
)
writeLines(validation_lines, file.path(data_root, "fig3_validation_summary.txt"))
cat(paste(validation_lines, collapse = "\n"), "\n")
