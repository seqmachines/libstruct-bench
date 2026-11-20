#!/usr/bin/env Rscript
# Reuse the original plotting blocks verbatim, supplying only locked 4.7.0
# observations/statistics. Never source the old scripts' scoring/data-writing
# sections. Removing ddSEQ affects the T2 visualization only, not saved data.
suppressPackageStartupMessages({
  library(dplyr); library(ggplot2); library(pheatmap)
  library(scales); library(jsonlite)
})
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args)==1L)
out <- normalizePath(args[[1]],mustWork=TRUE)
data_root <- dirname(out)
figure_root <- file.path(out,"figures")
source3 <- readLines(file.path(data_root,"plot_fig3_baselines.R"),warn=FALSE)
source4a <- readLines(file.path(data_root,"plot_fig4_external_knowledge.R"),warn=FALSE)
source4cd <- readLines(file.path(data_root,"plot_fig4cd_autonomous_improvement.R"),warn=FALSE)
block <- function(lines,start,end,include_end=TRUE) {
  first <- which(grepl(start,lines,fixed=TRUE)); stopifnot(length(first)==1L)
  last <- which(grepl(end,lines,fixed=TRUE)); last <- last[last>first][[1]]
  if (!include_end) last <- last-1L
  parse(text=lines[first:last])
}
open_pdf <- function(stem,width,height=5.25) {
  grDevices::pdf(file.path(figure_root,paste0(stem,".pdf")),width=width,height=height,
                family="Helvetica",paper="special",useDingbats=FALSE,bg="white")
}
metrics <- c("t2_required_family_f1","t3_molecular_transition_f1","t3_state_f1","t3_typed_edge_f1")
metric_labels <- c("Task 2 oligo-family F1","Task 3 transition F1","Task 3 state F1","Task 3 typed-edge F1")
plot_data <- read.csv(file.path(out,"fig3b_plot_data.csv"),stringsAsFactors=FALSE,check.names=FALSE)
aggregate <- read.csv(file.path(out,"aggregates.csv"),stringsAsFactors=FALSE,check.names=FALSE)
aggregate <- aggregate[aggregate$policy=="primary",]
stopifnot(nrow(plot_data)==90L,!anyNA(plot_data[,metrics]))
system_key <- distinct(plot_data,group,system_label,model_annotation,harness_annotation,display_order) %>% arrange(display_order)
protocol_key <- distinct(plot_data,protocol_id,protocol_label,protocol_order) %>% arrange(protocol_order)
system_labels <- setNames(system_key$system_label,system_key$group)
protocol_labels <- setNames(protocol_key$protocol_label,protocol_key$protocol_id)
system_ids <- system_key$group
panel_b_annotation <- data.frame(
  Model=factor(system_key$model_annotation,levels=c("GPT","Claude","Gemini")),
  Harness=factor(system_key$harness_annotation,levels=c("Pi","mini-SWE","Codex","Claude Code","Antigravity CLI")),
  row.names=system_key$system_label
)
eval(block(source3,"panel_b_heatmap_colours <- colorRampPalette(","panel_b_fill_limits <- c(0.38, 1.00)"))
eval(block(source3,"panel_b_annotation_colours <- list(","# Panel C: output-discrepancy prevalence",FALSE))
make_matrix <- function(metric) {
  rows <- plot_data
  if (metric==metrics[[1]]) rows <- rows[rows$required_families>0,]
  protocols <- protocol_key$protocol_id[protocol_key$protocol_id %in% rows$protocol_id]
  mat <- matrix(NA_real_,nrow=length(system_ids),ncol=length(protocols),dimnames=list(system_ids,protocols))
  for (i in seq_len(nrow(rows))) mat[rows$group[[i]],rows$protocol_id[[i]]] <- rows[[metric]][[i]]
  stopifnot(!anyNA(mat),nrow(mat)==9L,ncol(mat)==ifelse(metric==metrics[[1]],9L,10L))
  correlation <- cor(t(mat),method="pearson",use="everything")
  stopifnot(!anyNA(correlation))
  correlation[correlation>1] <- 1; correlation[correlation< -1] <- -1
  cluster <- hclust(as.dist(1-correlation),method="average")
  order_ids <- cluster$labels[cluster$order]
  rownames(mat) <- unname(system_labels[rownames(mat)])
  colnames(mat) <- unname(protocol_labels[colnames(mat)])
  cluster$labels <- unname(system_labels[cluster$labels])
  numbers <- matrix(sprintf("%.2f",as.numeric(mat)),nrow=nrow(mat),ncol=ncol(mat),dimnames=dimnames(mat))
  list(matrix=mat,numbers=numbers,cluster=cluster,annotation=panel_b_annotation[rownames(mat),,drop=FALSE],
       systems=order_ids,protocols=protocols)
}
main <- make_matrix(metrics[[2]])
panel_b_matrix <- main$matrix;panel_b_numbers <- main$numbers
panel_b_cluster <- main$cluster
stopifnot(min(panel_b_matrix)>=panel_b_fill_limits[[1]],max(panel_b_matrix)<=panel_b_fill_limits[[2]])
open_pdf("fig3b_t3_heatmap",10.5)
eval(block(source3,"  panel_b_heatmap <- pheatmap(","  grDevices::dev.off()"))
orders <- list(transition=list(systems=main$systems,protocols=main$protocols))
stems <- c("fig3b_t2_heatmap","fig3b_t3_state_heatmap","fig3b_t3_typed_edge_heatmap")
titles <- c("Task 2 oligo-family F1","Task 3 molecular-state F1","Task 3 typed-edge F1")
for (j in seq_along(stems)) {
  i <- c(1,3,4)[[j]]
  panel_key <- c("t2","state","edge")[[j]]
  metric_result <- make_matrix(metrics[[i]])
  metric_result$title <- titles[[j]]
  open_pdf(stems[[j]],11)
  eval(block(source3,"  metric_heatmap <- pheatmap(","  grDevices::dev.off()"))
  orders[[panel_key]] <- list(systems=metric_result$systems,protocols=metric_result$protocols)
}
write_json(list(method="Original R design: independent average-linkage clustering by Pearson correlation distance for each metric; fixed original protocol order; ddSEQ omitted only from T2.",
                heatmaps=orders,main_transition_fill_limits=panel_b_fill_limits,supplementary_fill_limits=c(0,1)),
           file.path(out,"heatmap_order.json"),pretty=TRUE,auto_unbox=TRUE)

# Feed corrected aggregate means/CIs to the exact original Figure 4 blocks.
stats <- function(panel) {
  x <- aggregate[aggregate$panel==panel,]
  x$metric_label <- metric_labels[match(x$metric,metrics)]
  x$mean_score <- x$mean; x$mean_label <- sprintf("%.2f",x$mean)
  x$label_y <- x$ci_high+0.018
  x
}
metric_key <- data.frame(metric_label=metric_labels,metric_axis_label=c("Task 2 oligo-family\nF1","Task 3 transition\nF1","Task 3 state\nF1","Task 3 typed-edge\nF1"))
condition_key <- data.frame(condition_label=c("Baseline","Textbook knowledge","Cross-protocol memory","Knowledge + memory"))
pilot_summary <- stats("fig4a")
pilot_summary$condition_label <- condition_key$condition_label[match(pilot_summary$group,c("baseline","general_methods_v1","cross_protocol_memory_v1","general_methods_plus_memory_v1"))]
stopifnot(nrow(pilot_summary)==16L,all(pilot_summary$n_protocols==ifelse(pilot_summary$metric==metrics[[1]],4L,5L)))
eval(block(source4a,"condition_levels <- condition_key$condition_label","grDevices::dev.off()"))

metric_key$metric_axis_label <- c("Task 2\noligo-family\nF1","Task 3\ntransition\nF1","Task 3\nstate\nF1","Task 3\ntyped edge\nF1")
learning_summary <- stats("fig4c")
learning_summary$training_protocols <- as.integer(sub("C","",learning_summary$group))
stopifnot(nrow(learning_summary)==52L,all(learning_summary$n_protocols==ifelse(learning_summary$metric==metrics[[1]],4L,5L)),all(learning_summary$mean_score>=.4 & learning_summary$mean_score<=.9))
panels_to_write <- c("c","d")
eval(block(source4cd,"metric_colours <- c(","# Compare the same ten held-out protocols",FALSE))
test_summary <- stats("fig4d")
test_summary$condition_label <- c("C0 baseline","C30 rules only","C30 rules + KB")[match(test_summary$group,c("c0","c30_pact","c30_kb"))]
stopifnot(nrow(test_summary)==12L,all(test_summary$n_protocols==10L))
eval(block(source4cd,"condition_colours <- c(","validation_lines <- c(",FALSE))
cat("Four Fig. 3 heatmaps and Fig. 4a/c/d rendered from locked scores in original R styles.\n")
