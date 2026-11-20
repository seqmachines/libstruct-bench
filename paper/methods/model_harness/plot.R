#!/usr/bin/env Rscript
suppressPackageStartupMessages(library(ggplot2))
args <- commandArgs(trailingOnly=TRUE);out <- normalizePath(args[[1]])
d <- read.csv(file.path(out,'primary_effects.csv'))
metrics <- c('t2_required_family_f1','t3_molecular_transition_f1','t3_state_f1','t3_typed_edge_f1')
labels <- c('Oligo-family F1 (9 protocols)','Transition F1 (10 protocols)','State F1 (10 protocols)','Typed-edge F1 (10 protocols)')
d$metric_label <- factor(labels[match(d$metric,metrics)],levels=rev(labels))
theme_set(theme_classic(base_size=11,base_family='Helvetica'))
common <- theme(axis.text=element_text(colour='#222222'),plot.title=element_text(size=13),legend.position='bottom',plot.margin=margin(8,10,4,5))
left <- d[d$effect!='model_minus_harness',]
left$effect <- factor(left$effect,levels=c('model_switch','harness_switch'),labels=c('Model switch','Harness switch'))
p1 <- ggplot(left,aes(x=estimate,y=metric_label,colour=effect)) +
 geom_errorbar(aes(xmin=ci_low,xmax=ci_high),position=position_dodge(width=.42),orientation='y',width=.12,linewidth=.5) +
 geom_point(position=position_dodge(width=.42),size=2.4) +
 scale_colour_manual(values=c('#0072b2','#e69f00'),name=NULL) + scale_x_continuous(limits=c(0,max(left$ci_high)*1.07)) +
 labs(title='A  Paired switch magnitudes',x='Mean absolute F1 difference',y=NULL) + common
right <- d[d$effect=='model_minus_harness',]
p2 <- ggplot(right,aes(x=estimate,y=metric_label)) + geom_vline(xintercept=0,colour='#888888',linetype='dashed',linewidth=.4) +
 geom_errorbar(aes(xmin=ci_low,xmax=ci_high),orientation='y',width=.13,linewidth=.55,colour='#333333') +
 geom_point(size=2.5,colour='#009e73') +
 labs(title='B  Model minus harness',x='Difference in mean switch magnitude',y=NULL) + common +
 theme(axis.text.y=element_blank(),axis.ticks.y=element_blank())
dir.create(file.path(out,'figures'),showWarnings=FALSE)
pdf(file.path(out,'figures/model_vs_harness_magnitudes.pdf'),width=10,height=4.2,family='Helvetica',useDingbats=FALSE)
grid::grid.newpage();grid::pushViewport(grid::viewport(layout=grid::grid.layout(2,2,widths=c(.6,.4),heights=c(.92,.08))))
g1 <- ggplotGrob(p1);g2 <- ggplotGrob(p2)
stopifnot(length(g1$heights)==length(g2$heights))
heights <- grid::unit.pmax(g1$heights,g2$heights);g1$heights <- heights;g2$heights <- heights
grid::pushViewport(grid::viewport(layout.pos.row=1,layout.pos.col=1));grid::grid.draw(g1);grid::popViewport()
grid::pushViewport(grid::viewport(layout.pos.row=1,layout.pos.col=2));grid::grid.draw(g2);grid::popViewport()
grid::grid.text('95% paired protocol-bootstrap intervals; one saved attempt per model-harness-protocol cell.',
               vp=grid::viewport(layout.pos.row=2,layout.pos.col=1:2),gp=grid::gpar(fontfamily='Helvetica',fontsize=9,col='#333333'))
dev.off()
