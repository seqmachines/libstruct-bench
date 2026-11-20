#!/usr/bin/env Rscript
# Presentation only: consume verified summaries, without fitting or scoring.
# Rscript plot_publication.R results_v1 publication_figures/model_vs_harness_magnitudes.pdf
suppressPackageStartupMessages({library(grid);library(jsonlite)})
args <- commandArgs(trailingOnly=TRUE);stopifnot(length(args)==2L)
source <- normalizePath(args[[1]],mustWork=TRUE)
destination <- args[[2]]
stopifnot(tolower(tools::file_ext(destination))=='pdf')
manifest <- fromJSON(file.path(source,'manifest.json'))
input <- file.path(source,'summary_table.csv')
actual_hash <- strsplit(system2('shasum',c('-a','256',shQuote(input)),stdout=TRUE),'[[:space:]]+')[[1]][[1]]
stopifnot(identical(actual_hash,manifest$artifacts[['summary_table.csv']]))
d <- read.csv(input,stringsAsFactors=FALSE,check.names=FALSE)
metric_order <- c('Task 2 oligo-family F1','Task 3 transition F1','Task 3 state F1','Task 3 typed-edge F1')
d <- d[match(metric_order,d$Metric),]
stopifnot(nrow(d)==4L,!anyNA(d),identical(as.integer(d$Protocols),c(9L,10L,10L,10L)))
# Bonferroni across the four primary metrics; retain the verified raw tests.
d$Bonferroni_p <- p.adjust(d$Signflip_p,method='bonferroni',n=4L)
stopifnot(all(d$Bonferroni_p[-3]>.05),d$Bonferroni_p[[3]]==.015625)

# The manuscript's neurips_2026.sty specifies textwidth=5.5in.
W <- 5.5*72;H <- 3.2*72
model_colour <- '#3B6C8E';harness_colour <- '#B3834C'
ink <- '#252A2E';muted <- '#616A71';rule <- '#9AA3A9'
rows <- c(166,132,98,64)
A <- c(94,231);B <- c(267,387)
ax <- function(x) A[[1]]+x/.20*diff(A)
bx <- function(x) B[[1]]+(x+.04)/.18*diff(B)
pt <- function(x) unit(x,'points')
line <- function(x0,y0,x1,y1,colour=ink,width=.65,type='solid') {
    grid.lines(x=pt(c(x0,x1)),y=pt(c(y0,y1)),gp=gpar(col=colour,lwd=width,lty=type,lineend='butt'))
}
label <- function(text,x,y,size=8,colour=ink,face='plain',just='left') {
    grid.text(text,x=pt(x),y=pt(y),just=just,gp=gpar(fontfamily='Helvetica',fontsize=size,col=colour,fontface=face))
}
dot <- function(x,y,colour,r=2.8) grid.circle(x=pt(x),y=pt(y),r=pt(r),gp=gpar(fill=colour,col='white',lwd=.45))
dir.create(dirname(destination),recursive=TRUE,showWarnings=FALSE)
pdf(destination,width=W/72,height=H/72,family='Helvetica',paper='special',useDingbats=FALSE,bg='white',title='Model-switch and harness-switch effect magnitudes')
grid.newpage()
pushViewport(viewport(y=pt(7.2),height=pt(H-7.2),just='bottom'))

# A single subtle state row highlight spans the shared metric label and panels.
grid.rect(x=pt(2),y=pt(rows[[3]]-17),width=pt(W-5),height=pt(32),just=c('left','bottom'),gp=gpar(fill='#EEF3F6',col=NA))
label('Switch magnitudes',A[[1]],211,size=9.2)
# R's PDF device maps ASCII '-' to minus; code 173 selects its actual hyphen glyph.
label('Model\u00adharness',B[[1]],211,size=9.2)

dot(A[[1]]+3,192,model_colour,r=2.3);label('Model switch',A[[1]]+10,192,size=7.2)
dot(A[[1]]+78,192,harness_colour,r=2.3);label('Harness switch',A[[1]]+85,192,size=7.2)
label('Paired estimate and 95% CI',B[[1]],192,size=7.1,colour=muted)

metric_labels <- c('T2  Oligo','T3  Transition','T3  State','T3  Typed edge')
for(i in seq_len(4)) {
    label(metric_labels[[i]],6,rows[[i]]+1,size=8.6,face=if(i==3) 'bold' else 'plain')
    label(paste0('n = ',d$Protocols[[i]],' protocols'),6,rows[[i]]-10,size=7.2,colour=muted)
    # Dumbbell endpoints are the existing means; no confidence bars in panel A.
    line(ax(d$Harness_switch[[i]]),rows[[i]],ax(d$Model_switch[[i]]),rows[[i]],colour='#AEB7BD',width=1.1)
    dot(ax(d$Harness_switch[[i]]),rows[[i]],harness_colour)
    dot(ax(d$Model_switch[[i]]),rows[[i]],model_colour)
}

# Forest intervals come directly from the paired D_p bootstrap summary.
line(bx(0),48,bx(0),180,colour=rule,width=.65,type='dashed')
for(i in seq_len(4)) {
    colour <- if(i==3) model_colour else '#515B63'
    line(bx(d$CI_low[[i]]),rows[[i]],bx(d$CI_high[[i]]),rows[[i]],colour=colour,width=if(i==3) 1.05 else .75)
    for(endpoint in c(d$CI_low[[i]],d$CI_high[[i]])) line(bx(endpoint),rows[[i]]-2.4,bx(endpoint),rows[[i]]+2.4,colour=colour,width=.7)
    dot(bx(d$Difference[[i]]),rows[[i]],colour,r=if(i==3) 3.2 else 2.6)
}
label(sprintf('Bonferroni p = %.4f',d$Bonferroni_p[[3]]),bx(d$Difference[[3]]),rows[[3]]-10,size=7.3,colour=model_colour,just='centre')

for(bounds in list(A,B))line(bounds[[1]],45,bounds[[2]],45,colour=ink,width=.65)
for(tick in seq(0,.20,.05)) {
    line(ax(tick),45,ax(tick),42,width=.65)
    label(sprintf('%.2f',tick),ax(tick),35,size=7.6,just='centre')
}
for(tick in seq(-.04,.12,.04)) {
    line(bx(tick),45,bx(tick),42,width=.65)
    label(sprintf('%.2f',tick),bx(tick),35,size=7.6,just='centre')
}
label('Mean absolute difference',mean(A),23,size=8,just='centre')
label('(F1 units)',mean(A),13.5,size=7.6,colour=muted,just='centre')
label('Paired difference',mean(B),23,size=8,just='centre')
label('(F1 units)',mean(B),13.5,size=7.6,colour=muted,just='centre')
invisible(dev.off())
cat('Publication PDF exported; all values read from the verified summary table.\n')
