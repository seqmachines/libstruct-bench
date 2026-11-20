#!/usr/bin/env Rscript
# Original Fig. 3a design from ../plot_fig3_baselines.R, Panel A.
# Corrected 4.7.0 statistics, informative Task 2, three-decimal bar labels.
# No bootstrapping, scoring, or changes to historical artifacts happen here.
suppressPackageStartupMessages({library(ggplot2); library(scales)})
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args)==2L)
data <- read.csv(args[[1]], stringsAsFactors=FALSE, check.names=FALSE)
data <- data[data$panel=="fig3a" & data$policy=="primary", ]
metrics <- c("t2_required_family_f1", "t3_molecular_transition_f1", "t3_state_f1", "t3_typed_edge_f1")
systems <- c("gpt_codex", "claude_code", "gemini_antigravity", "kimi_code")
labels <- c("Task 2 oligo-family F1", "Task 3 transition F1", "Task 3 state F1", "Task 3 typed-edge F1")
axis_labels <- setNames(c("GPT-5.6 Sol\nMax\nCodex", "Claude Opus 5\nMax\nClaude Code", "Gemini 3.7 Flash\nHigh\nAntigravity CLI", "Kimi K3\nMax\nKimi Code"), systems)
stopifnot(nrow(data)==16L, all(table(data$group)==4L), all(data$n_protocols[data$metric==metrics[[1]]]==19L), all(data$n_protocols[data$metric!=metrics[[1]]]==20L))
data$metric_order <- match(data$metric,metrics)
data$system <- factor(data$group,levels=systems)
data$metric_label <- factor(labels[data$metric_order],levels=labels)
data$mean_label <- sprintf("%.3f",data$mean)
data$label_y <- pmin(data$ci_high+0.04+c(0,0.015,0,0.025)[data$metric_order],1.08)
colours <- setNames(c("#56b4e9","#0072b2","#e69f00","#009e73"),labels)
plot <- ggplot(data,aes(x=system,y=mean,fill=metric_label)) +
  geom_col(position=position_dodge(width=0.78),width=0.68,colour="white",linewidth=0.25) +
  geom_errorbar(aes(ymin=ci_low,ymax=ci_high),position=position_dodge(width=0.78),width=0.15,linewidth=0.42,colour="#333333") +
  geom_text(aes(y=label_y,label=mean_label),position=position_dodge(width=0.78),vjust=0,size=3.2,colour="#222222") +
  scale_fill_manual(values=colours,name=NULL) +
  scale_x_discrete(labels=axis_labels) +
  scale_y_continuous(limits=c(0,1.10),breaks=seq(0,1,0.2),labels=number_format(accuracy=0.1),expand=expansion(mult=c(0,0))) +
  labs(title="Native CLI baseline performance",x=NULL,y="Mean score") +
  theme_classic(base_size=13.5,base_family="Helvetica") +
  theme(axis.title.y=element_text(size=16),axis.text=element_text(size=12.8,colour="#222222"),axis.text.x=element_text(size=11.2,lineheight=0.96),plot.title=element_text(size=17.5,face="plain",colour="#222222"),legend.position="bottom",legend.text=element_text(size=9.4),legend.key.width=grid::unit(2.0,"mm"),legend.spacing.x=grid::unit(0.2,"mm"),plot.margin=margin(6,7,3,6)) +
  guides(fill=guide_legend(nrow=1,byrow=TRUE))
grDevices::pdf(paste0(args[[2]],".pdf"),width=6.4,height=4.65,family="Helvetica",paper="special",useDingbats=FALSE,bg="white")
print(plot); invisible(dev.off())
# The original 9.8-point legend clips its last label at this figure width;
# 9.4 points retains the original one-row design without clipping.
# Python converts the final PDF to SVG/PNG using Poppler, avoiding optional
# R/X11/Cairo device dependencies and guaranteeing the same rendered layout.
cat("Fig. 3a: original R style; corrected 4.7.0 means/CIs; Task 2 n=19, Task 3 n=20.\n")
