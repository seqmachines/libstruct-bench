#!/usr/bin/env Rscript
# Secondary model-based inference and protocol-blocked repeated-measures checks.
suppressPackageStartupMessages(library(nlme))
args <- commandArgs(trailingOnly=TRUE);stopifnot(length(args)==1L)
out <- normalizePath(args[[1]]);options(contrasts=c('contr.sum','contr.poly'))
d <- read.csv(file.path(out,'shared_long.csv'),stringsAsFactors=FALSE)
w <- read.csv(file.path(out,'contrast_weights.csv'),stringsAsFactors=FALSE,check.names=FALSE)
models <- c('GPT','Claude','Gemini');harnesses <- c('Pi','mini-SWE')
effects <- list();coefficients <- list();diagnostics <- list();omnibus <- list();blocked <- list();covariances <- list()
grid <- data.frame(model=factor(rep(models,each=2),levels=models),harness=factor(rep(harnesses,3),levels=harnesses))
X <- model.matrix(~model*harness,grid)
for(metric in unique(d$metric)) {
  v <- d[d$metric==metric,];v$model <- factor(v$model,levels=models);v$harness <- factor(v$harness,levels=harnesses);v$protocol_id <- factor(v$protocol_id)
  n <- nlevels(v$protocol_id);stopifnot(nrow(v)==6*n,all(table(v$protocol_id,v$model,v$harness)==1))
  caught <- character()
  fit <- withCallingHandlers(lme(score~model*harness,random=~1|protocol_id,data=v,method='REML',na.action=na.fail,
              control=lmeControl(maxIter=200,msMaxIter=200,msMaxEval=400,returnObject=FALSE)),
              warning=function(e){caught <<- c(caught,conditionMessage(e));invokeRestart('muffleWarning')})
  beta <- fixef(fit);V <- vcov(fit);stopifnot(identical(colnames(X),names(beta)))
  for(i in seq_len(nrow(w))) {
    L <- as.numeric(as.matrix(w[i,paste0('w',0:5)]) %*% X)
    estimate <- sum(L*beta);se <- sqrt(drop(L %*% V %*% L));z <- estimate/se
    effects[[length(effects)+1]] <- data.frame(metric=metric,contrast=w$contrast[[i]],kind=w$kind[[i]],estimate=estimate,
       mixed_se=se,mixed_wald_ci_low=estimate-qnorm(.975)*se,mixed_wald_ci_high=estimate+qnorm(.975)*se,
       mixed_wald_z=if(grepl('mean',w$kind[[i]])) NA_real_ else z,
       mixed_wald_p=if(grepl('mean',w$kind[[i]])) NA_real_ else 2*pnorm(-abs(z)),
       uncertainty='Asymptotic model-based Wald; protocol bootstrap is primary')
  }
  ct <- as.data.frame(summary(fit)$tTable);ct$term <- rownames(ct);ct$metric <- metric;coefficients[[metric]] <- ct
  vc <- VarCorr(fit)
  diagnostics[[metric]] <- data.frame(metric=metric,n_protocols=n,n_observations=nrow(v),method='REML',
       random_intercept_variance=as.numeric(vc[1,'Variance']),residual_variance=as.numeric(vc[2,'Variance']),
       log_likelihood=as.numeric(logLik(fit)),iterations=if(length(fit$numIter)) fit$numIter else NA_integer_,
       warnings=paste(caught,collapse=' | '),min_fixed_prediction=min(as.vector(X%*%beta)),max_fixed_prediction=max(as.vector(X%*%beta)))
  cv <- expand.grid(term1=rownames(V),term2=colnames(V));cv$covariance <- as.vector(V);cv$metric <- metric;covariances[[metric]] <- cv
  an <- as.data.frame(anova(fit,type='marginal'));an$term <- rownames(an);an$metric <- metric;omnibus[[metric]] <- an
  # Separate protocol x model, protocol x harness, protocol x interaction errors.
  rm <- aov(score~model*harness+Error(protocol_id/(model*harness)),data=v)
  sr <- summary(rm)
  score <- matrix(NA_real_,nrow=n,ncol=6)
  for(i in seq_len(nrow(v)))score[as.integer(v$protocol_id[[i]]),(as.integer(v$model[[i]])-1)*2+as.integer(v$harness[[i]])] <- v$score[[i]]
  helm <- contr.helmert(3);helm <- sweep(helm,2,sqrt(colSums(helm^2)),'/')
  model_means <- (score[,c(1,3,5)]+score[,c(2,4,6)])/2
  harness_differences <- score[,c(2,4,6)]-score[,c(1,3,5)]
  coords <- list(model=model_means%*%helm,harness=matrix(rowMeans(harness_differences),ncol=1),'model:harness'=harness_differences%*%helm)
  for(term in names(coords)) {
    stratum <- paste0('Error: protocol_id:',term)
    tab <- sr[[stratum]][[1]];rownames(tab) <- trimws(rownames(tab));stopifnot(term %in% rownames(tab))
    num <- tab[term,'Df'];den <- tab['Residuals','Df'];F <- tab[term,'F value'];P <- tab[term,'Pr(>F)']
    z <- coords[[term]];q <- ncol(z);S <- matrix(cov(z),q,q)
    epsilon <- if(q==1 || sum(S*S)==0) 1 else max(1/q,min(1,sum(diag(S))^2/(q*sum(S*S))))
    manual <- n*sum(colMeans(z)^2)/sum(diag(S));stopifnot(abs(manual-F)<1e-8)
    blocked[[length(blocked)+1]] <- data.frame(metric=metric,term=term,n_protocols=n,numerator_df=num,denominator_df=den,F=F,
          uncorrected_p=P,GG_epsilon=epsilon,GG_numerator_df=num*epsilon,GG_denominator_df=den*epsilon,
          GG_p=pf(F,num*epsilon,den*epsilon,lower.tail=FALSE),
          partial_eta_squared=tab[term,'Sum Sq']/(tab[term,'Sum Sq']+tab['Residuals','Sum Sq']))
  }
}
write <- function(rows,name) write.csv(do.call(rbind,rows),file.path(out,name),row.names=FALSE,na='')
write(effects,'mixed_effects.csv');write(coefficients,'mixed_coefficients.csv');write(covariances,'mixed_fixed_covariance.csv')
write(diagnostics,'mixed_diagnostics.csv');write(omnibus,'mixed_omnibus.csv')
blocks <- do.call(rbind,blocked);blocks$GG_p_holm_12 <- p.adjust(blocks$GG_p,method='holm')
write.csv(blocks,file.path(out,'blocked_factorial.csv'),row.names=FALSE)
writeLines(capture.output(sessionInfo()),file.path(out,'R_session_info.txt'))
cat('Four random-intercept REML fits and protocol-blocked factorial checks completed.\n')
