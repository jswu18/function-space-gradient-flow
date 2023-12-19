from typing import Tuple

import gpytorch
import torch

from experiments.data import Data, ExperimentData
from src.gps import ExactGP, svGP
from src.gradient_flows import ProjectedWassersteinGradientFlow
from src.kernels import GradientFlowKernel


def load_projected_wasserstein_gradient_flow(
    model_path: str,
    base_kernel: gpytorch.kernels.Kernel,
    observation_noise: float,
    experiment_data: ExperimentData,
    induce_data: Data,
    jitter: float,
) -> (ProjectedWassersteinGradientFlow, torch.Tensor):
    model_config = torch.load(model_path)
    particles = model_config["particles"].to(torch.double)
    pwgf = ProjectedWassersteinGradientFlow(
        number_of_particles=particles.shape[1],
        kernel=GradientFlowKernel(
            base_kernel=base_kernel,
            approximation_samples=experiment_data.train.x,
        ),
        x_induce=induce_data.x,
        y_induce=induce_data.y,
        x_train=experiment_data.train.x,
        y_train=experiment_data.train.y,
        jitter=jitter,
        observation_noise=observation_noise,
    )
    pwgf.particles = particles
    print(f"Loaded PWGF model from {model_path=}.")
    return pwgf


def load_svgp(
    model_path: str,
    x_induce: torch.Tensor,
    mean: gpytorch.means.Mean,
    kernel: gpytorch.kernels.Kernel,
    learn_inducing_locations: bool,
) -> Tuple[svGP, torch.Tensor]:
    model = svGP(
        x_induce=x_induce.to(torch.double),
        mean=mean,
        kernel=kernel,
        learn_inducing_locations=learn_inducing_locations,
        likelihood=gpytorch.likelihoods.GaussianLikelihood(),
    )
    loaded_states = torch.load(model_path)
    model.load_state_dict(loaded_states["model"])
    model.double()
    print(f"Loaded svGP model from {model_path=}.")
    return model, loaded_states["losses"]


def load_ard_exact_gp_model(
    model_path: str,
    data_path: str,
) -> Tuple[ExactGP, torch.Tensor]:
    data = torch.load(data_path)
    data.x.to(torch.double)
    data.y.to(torch.double)
    model_state_dict = torch.load(model_path)
    model = ExactGP(
        x=data.x,
        y=data.y,
        likelihood=gpytorch.likelihoods.GaussianLikelihood(),
        mean=gpytorch.means.ConstantMean(),
        kernel=gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.RBFKernel(ard_num_dims=data.x.shape[1])
        ),
    )
    model.load_state_dict(model_state_dict["model"])
    print(f"Loaded model from {model_path=} and from {data_path=}.")
    return model, model_state_dict["losses"]
