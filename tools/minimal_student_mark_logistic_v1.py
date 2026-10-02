"""Small deterministic logistic learner for observed-fill auxiliary research only.

No execution authority, policy semantics, dynamic dependency installation or HFT.
"""
import math
import numpy as np

REGULARIZATION=0.1


def sigmoid(z):
    z=np.asarray(z,dtype=float)
    a=np.exp(-np.abs(z))
    return np.where(z>=0,1./(1.+a),a/(1.+a))


def objective_gradient_hessian(a,y,w,reg=REGULARIZATION):
    z=a@w;p=sigmoid(z)
    penalty=np.ones(w.shape);penalty[0]=0.
    loss=float(np.mean(np.logaddexp(0.,z)-y*z)+.5*reg*np.dot(w*penalty,w))
    gradient=a.T@(p-y)/len(y)+reg*penalty*w
    hessian=(a.T*(p*(1.-p)))@a/len(y)+np.diag(reg*penalty)
    return loss,gradient,hessian


def fit(x,y,reg=REGULARIZATION,max_iter=100,tolerance=1e-7):
    x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float)
    if x.ndim!=2 or y.ndim!=1 or len(x)!=len(y) or len(y)<2:
        raise ValueError('invalid fit arrays')
    if not np.isfinite(x).all() or not np.isfinite(y).all() or not np.isin(y,[0.,1.]).all():
        raise ValueError('finite binary inputs required')
    freq=float(y.mean())
    if not 0<freq<1:raise ValueError('TRAIN constant heads are excluded, not fabricated')
    a=np.column_stack([np.ones(len(x)),x]);w=np.zeros(a.shape[1])
    w[0]=math.log(freq/(1.-freq));history=[];converged=False;steps=0
    for iteration in range(max_iter+1):
        loss,g,h=objective_gradient_hessian(a,y,w,reg)
        if not (np.isfinite(loss) and np.isfinite(g).all() and np.isfinite(h).all()):
            raise ArithmeticError('nonfinite optimization state')
        history.append(loss);norm=float(np.max(np.abs(g)))
        if norm<tolerance:converged=True;break
        if iteration==max_iter:break
        direction=np.linalg.solve(h+1e-12*np.eye(len(w)),g)
        descent=float(g@direction)
        if not descent>0:raise ArithmeticError('non-descent Newton direction')
        accepted=False
        for k in range(25):
            step=2.**(-k);candidate=w-step*direction
            nxt=objective_gradient_hessian(a,y,candidate,reg)[0]
            if np.isfinite(nxt) and nxt<=loss-1e-4*step*descent:
                w=candidate;accepted=True;steps+=1;break
        if not accepted:raise ArithmeticError('numeric line search failed')
    if not converged:raise ArithmeticError('prespecified optimizer did not converge')
    return dict(intercept=float(w[0]),coefficients=w[1:].tolist(),regularization=reg,
        converged=True,newton_steps=steps,gradient_inf=norm,
        initial_objective=history[0],final_objective=history[-1],
        objective_history=history,training_rows=len(y),training_frequency=freq)


def predict(model,x):
    x=np.asarray(x,dtype=float);coef=np.asarray(model['coefficients'],dtype=float)
    if x.ndim!=2 or x.shape[1]!=len(coef) or not np.isfinite(x).all():
        raise ValueError('prediction feature schema/values invalid')
    return sigmoid(float(model['intercept'])+x@coef)


def metrics(y,p):
    y=np.asarray(y,dtype=float);p=np.asarray(p,dtype=float)
    if y.ndim!=1 or y.shape!=p.shape or len(y)==0 or not np.isin(y,[0.,1.]).all():
        raise ValueError('invalid metric arrays')
    if not np.isfinite(p).all() or ((p<0)|(p>1)).any():raise ValueError('invalid probabilities')
    npos=int(y.sum());nneg=len(y)-npos;c=np.clip(p,1e-12,1.-1e-12)
    loss=float(-np.mean(y*np.log(c)+(1.-y)*np.log1p(-c)))
    brier=float(np.mean((p-y)**2));auc=None;ap=None
    if npos and nneg:
        differences=p[y==1][:,None]-p[y==0][None,:]
        auc=float(np.mean((differences>0)+.5*(differences==0)))
    if npos:
        order=np.argsort(-p,kind='stable');ps=p[order];ys=y[order]
        ends=np.r_[np.flatnonzero(ps[:-1]!=ps[1:]),len(ps)-1]
        tp=np.cumsum(ys)[ends];recall=tp/npos;precision=tp/(ends+1)
        ap=float(np.sum(np.diff(np.r_[0.,recall])*precision))
    return dict(rows=len(y),positive=npos,negative=nneg,prevalence=npos/len(y),
                log_loss=loss,brier=brier,roc_auc=auc,average_precision=ap)


def self_tests():
    tests=[];synthetic_fits=0
    def check(name,value):
        if not value:raise AssertionError(name)
        tests.append(name)
    m=metrics([0,1],[.5,.5])
    check('constant_probability_BCE_log2',abs(m['log_loss']-math.log(2))<1e-12)
    check('constant_probability_Brier_quarter',m['brier']==.25)
    check('constant_probability_tied_AUC_half',m['roc_auc']==.5)
    check('constant_probability_AP_prevalence',m['average_precision']==.5)
    check('perfect_AUC',metrics([0,1],[.1,.9])['roc_auc']==1.)
    check('inverse_AUC',metrics([0,1],[.9,.1])['roc_auc']==0.)
    m=metrics([0,0,1,1],[.1,.4,.35,.8])
    check('nontrivial_AUC_three_quarters',abs(m['roc_auc']-.75)<1e-12)
    check('nontrivial_AP_five_sixths',abs(m['average_precision']-5/6)<1e-12)
    check('one_class_AUC_null',metrics([1,1],[.3,.7])['roc_auc'] is None)
    check('no_positive_AP_null',metrics([0,0],[.3,.7])['average_precision'] is None)
    check('stable_extreme_logits',np.isfinite(sigmoid([-1000.,1000.])).all())
    a=np.array([[1.,-1.,.5],[1.,0.,2.],[1.,1.,-.5],[1.,2.,1.]])
    y=np.array([0.,1.,0.,1.]);w=np.array([.2,-.1,.3]);_,g,h=objective_gradient_hessian(a,y,w)
    gn=[]
    for k in range(len(w)):
        d=np.zeros(len(w));d[k]=1e-6
        gn.append((objective_gradient_hessian(a,y,w+d)[0]-objective_gradient_hessian(a,y,w-d)[0])/2e-6)
    check('gradient_finite_difference',np.max(np.abs(g-np.array(gn)))<1e-6)
    check('positive_definite_regularized_hessian',float(np.linalg.eigvalsh(h).min())>0.)
    x=np.array([[-2.],[-1.],[-.5],[.5],[1.],[2.]])
    y=np.array([0.,0.,1.,0.,1.,1.])
    model=fit(x,y);synthetic_fits+=1
    check('fit_converged',model['converged'])
    check('convex_objective_monotone',all(b<=a+1e-12 for a,b in zip(model['objective_history'],model['objective_history'][1:])))
    check('nonzero_signal_learned',model['coefficients'][0]>0)
    flip=fit(x,1.-y);synthetic_fits+=1
    check('fit_responds_to_labels',flip['coefficients'][0]<0)
    intercept=fit(np.empty((4,0)),[0.,0.,0.,1.]);synthetic_fits+=1
    check('intercept_equals_train_frequency',np.max(np.abs(predict(intercept,np.empty((4,0)))-.25))<1e-12)
    import json
    copy=json.loads(json.dumps(model))
    check('JSON_roundtrip_exact',np.max(np.abs(predict(copy,x)-predict(model,x)))<1e-12)
    try:fit(x,np.zeros(len(x)))
    except ValueError:check('constant_head_is_rejected',True)
    else:raise AssertionError('constant head accepted')
    return dict(passed=len(tests),tests=tests,synthetic_fit_count=synthetic_fits)
