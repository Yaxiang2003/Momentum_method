import math
import torch
from torch.optim.optimizer import Optimizer, required
import torch.nn as nn
import torch

def randomized_svd(A, rank):

    m, n = A.shape
    device = A.device
    random_matrix = torch.randn(size=(n, rank), device=device)
    datatype = A.dtype
    
    Y = A @ random_matrix.to(datatype)
    Q, _ = torch.linalg.qr(Y.float())
    Q = Q.to(datatype)
    B = Q.T @ A
    U_hat, S, V = torch.linalg.svd(B.float(), full_matrices=False)

    U = Q @ U_hat.to(datatype)
    S = S.to(datatype)
    V = V.to(datatype)

    return U, S, V

class MLorc_AdamW(Optimizer):
    def __init__(self, params, lr=1e-3, betas=(0.9, 0.999), eps=1e-8, weight_decay=0.01, correct_bias=True, rank=4):
        if lr < 0.0:
            raise ValueError("Invalid learning rate: {} - should be >= 0.0".format(lr))
        if not 0.0 <= betas[0] < 1.0:
            raise ValueError("Invalid beta parameter: {} - should be in [0.0, 1.0[".format(betas[0]))
        if not 0.0 <= betas[1] < 1.0:
            raise ValueError("Invalid beta parameter: {} - should be in [0.0, 1.0[".format(betas[1]))
        if not 0.0 <= eps:
            raise ValueError("Invalid epsilon value: {} - should be >= 0.0".format(eps))
        defaults = dict(lr=lr, betas=betas, eps=eps, weight_decay=weight_decay, correct_bias=correct_bias)
        self.rank=rank
        super().__init__(params, defaults)



    def step(self, closure=None):
        """Performs a single optimization step.
        Arguments:
            closure (callable, optional): A closure that reevaluates the model
                and returns the loss.
        """
        loss = None
        if closure is not None:
            loss = closure()

        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue
                grad = p.grad.data
                p.grad = None

                if grad.dim() != 2:
                    continue
                if grad.is_sparse:
                    raise RuntimeError("Adam does not support sparse gradients, please consider SparseAdam instead")
                    
                state = self.state[p]
                # State initialization
                if len(state) == 0:
                    state["step"] = 0
                    # Exponential moving average of gradient values
                    state["m_u"] = torch.zeros((p.data.shape[0], self.rank), dtype=p.data.dtype, device=p.data.device)
                    state["m_v"] = torch.zeros((self.rank, p.data.shape[1]), dtype=p.data.dtype, device=p.data.device)
                    state["m_s"] = torch.zeros((self.rank), dtype=p.data.dtype, device=p.data.device)
                    # Exponential moving average of squared gradient values
                    state["sq_u"] = torch.zeros((p.data.shape[0], self.rank), dtype=p.data.dtype, device=p.data.device)
                    state["sq_v"] = torch.zeros((self.rank, p.data.shape[1]), dtype=p.data.dtype, device=p.data.device)
                    state["sq_s"] = torch.zeros((self.rank), dtype=p.data.dtype, device=p.data.device)

                m_u, m_v, m_s, sq_u, sq_v, sq_s = state["m_u"], state["m_v"], state["m_s"], state["sq_u"], state["sq_v"], state["sq_s"]

                beta1, beta2 = group["betas"]

                state["step"] += 1

                m=beta1 * m_u @ torch.diag(m_s) @ m_v + (1-beta1) * grad
                sq=beta2 * sq_u @ torch.diag(sq_s) @ sq_v + (1-beta2) * grad * grad

                m_u, m_s, m_v = randomized_svd(m, self.rank)
                sq_u, sq_s, sq_v = randomized_svd(sq, self.rank)

                # Decay the first and second moment running average coefficient
                # In-place operations to update the averages at the same time
                denom = sq.sqrt().add_(group["eps"])

                step_size = group["lr"]
                if 'correct_bias' in group and group["correct_bias"]:  # No bias correction for Bert
                    bias_correction1 = 1.0 - beta1 ** state["step"]
                    bias_correction2 = 1.0 - beta2 ** state["step"]
                    step_size = step_size * math.sqrt(bias_correction2) / bias_correction1

                p.data.addcdiv_(-step_size, m, denom)

                # Just adding the square of the weights to the loss function is *not*
                # the correct way of using L2 regularization/weight decay with Adam,
                # since that will interact with the m and v parameters in strange ways.
                #
                # Instead we want to decay the weights in a manner that doesn't interact
                # with the m/v parameters. This is equivalent to adding the square
                # of the weights to the loss with plain (non-momentum) SGD.
                # Add weight decay at the end (fixed version)
                if group["weight_decay"] > 0.0:
                    p.data.add_(p.data, alpha=-group["lr"] * group["weight_decay"])

        return loss


class MLorc_Lion(Optimizer):
    def __init__(self, params, lr=1e-3, betas=(0.95, 0.98), weight_decay=0.05,  rank=4):
        defaults = dict(lr=lr, betas=betas, weight_decay=weight_decay)
        self.rank=rank
        super().__init__(params, defaults)



    def step(self, closure=None):
        """Performs a single optimization step.
        Arguments:
            closure (callable, optional): A closure that reevaluates the model
                and returns the loss.
        """
        loss = None
        if closure is not None:
            loss = closure()

        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue
                grad = p.grad.data

                if grad.dim() != 2:
                    continue
                if grad.is_sparse:
                    raise RuntimeError("Adam does not support sparse gradients, please consider SparseAdam instead")
                    
                state = self.state[p]
                # State initialization
                if len(state) == 0:
                    state["step"] = 0
                    # Exponential moving average of gradient values

                    state["m_u"] = torch.zeros((p.data.shape[0], self.rank), dtype=p.data.dtype, device=p.data.device)
                    state["m_v"] = torch.zeros((self.rank, p.data.shape[1]), dtype=p.data.dtype, device=p.data.device)
                    state["m_s"] = torch.zeros((self.rank), dtype=p.data.dtype, device=p.data.device)

                m_u, m_v, m_s= state["m_u"], state["m_v"], state["m_s"]
                beta1, beta2 = group["betas"]

                m=m_u @ torch.diag(m_s) @ m_v
                update=(beta1 * m + (1-beta1) * grad).sign_()

                state["step"] += 1
                step_size = group["lr"]
                p.data.add_(update, alpha=-step_size)

                m_=beta2 * m + (1-beta2) * grad
                m_u, m_s, m_v = randomized_svd(m_, self.rank)

                if group["weight_decay"] > 0.0:
                    p.data.add_(p.data, alpha=-group["lr"] * group["weight_decay"])

        return loss


class GaLore(Optimizer):
    def __init__(self, params, lr=1e-3, betas=(0.9, 0.999), eps=1e-8, weight_decay=0.01, correct_bias=True, rank=4, T=100):
        defaults = dict(lr=lr, betas=betas, eps=eps, weight_decay=weight_decay, correct_bias=correct_bias)
        self.rank=rank
        self.T=T
        super().__init__(params, defaults)



    def step(self, closure=None):
        """Performs a single optimization step.
        Arguments:
            closure (callable, optional): A closure that reevaluates the model
                and returns the loss.
        """
        loss = None
        if closure is not None:
            loss = closure()

        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue
                grad = p.grad.data

                if grad.dim() != 2:
                    continue
                if grad.is_sparse:
                    raise RuntimeError("Adam does not support sparse gradients, please consider SparseAdam instead")


                state = self.state[p]

                # State initialization
                if len(state) == 0:
                    state["step"] = 0
                    # Exponential moving average of gradient values
                    state["exp_avg"] = torch.zeros((self.rank, p.data.shape[1]), dtype=p.data.dtype, device=p.data.device)
                    state["exp_avg_sq"] = torch.zeros((self.rank, p.data.shape[1]), dtype=p.data.dtype, device=p.data.device)
                    state["projector"] = torch.zeros((p.data.shape[0], self.rank), dtype=p.data.dtype, device=p.data.device)

                exp_avg, exp_avg_sq = state["exp_avg"], state["exp_avg_sq"]
                beta1, beta2 = group["betas"]

                state["step"] += 1

                if(state["step"]%self.T==1):

                    u, s, v=torch.linalg.svd(grad.float(), full_matrices=False)
                    state["projector"] = u[:, :self.rank].bfloat16()

                Projector = state["projector"]
                R_=Projector.T @ grad

                # Decay the first and second moment running average coefficient
                # In-place operations to update the averages at the same time
                exp_avg.mul_(beta1).add_(R_, alpha=1.0 - beta1)
                exp_avg_sq.mul_(beta2).addcmul_(R_, R_, value=1.0 - beta2)
                denom = exp_avg_sq.sqrt().add_(group["eps"])

                step_size = group["lr"]
                if 'correct_bias' in group and group["correct_bias"]:  # No bias correction for Bert
                    bias_correction1 = 1.0 - beta1 ** state["step"]
                    bias_correction2 = 1.0 - beta2 ** state["step"]
                    step_size = step_size * math.sqrt(bias_correction2) / bias_correction1

                grad_d=torch.div(exp_avg, denom)
                u_grad_d= -step_size * Projector @ grad_d

                p.data.add_(u_grad_d)


                if group["weight_decay"] > 0.0:
                    p.data.add_(p.data, alpha=-group["lr"] * group["weight_decay"])

        return loss

       
