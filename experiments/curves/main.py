import argparse
import math
import os
from typing import Any, Dict

import gpytorch
import matplotlib.pyplot as plt
import torch
import yaml

from experiments.curves.curves import CURVE_FUNCTIONS, Curve
from experiments.data import Data, ExperimentData
from experiments.metrics import (
    calculate_particle_metrics,
    calculate_svgp_metrics,
    concatenate_metrics,
)
from experiments.plotters import plot_1d_experiment_data
from experiments.preprocess import split_regression_data_intervals
from experiments.runners import (
    optimise_kernel_and_induce_data,
    projected_wasserstein_gradient_flow,
    train_svgp,
)
from src.induce_data_selectors import ConditionalVarianceInduceDataSelector

parser = argparse.ArgumentParser(description="Main script for toy curves experiments.")
parser.add_argument("--config_path", type=str)


def get_experiment_data(
    data_config: Dict[str, Any],
    curve_function: Curve,
) -> ExperimentData:
    x = torch.linspace(-2, 2, data_config["number_of_data_points"]).reshape(-1, 1)
    y = curve_function(
        seed=data_config["seed"],
        x=x,
        sigma_true=data_config["sigma_true"],
    )
    (
        x_train,
        y_train,
        x_test,
        y_test,
        x_validation,
        y_validation,
    ) = split_regression_data_intervals(
        seed=data_config["seed"],
        split_seed=curve_function.seed,
        x=x,
        y=y,
        number_of_test_intervals=data_config["number_of_test_intervals"],
        total_number_of_intervals=data_config["total_number_of_intervals"],
        train_data_percentage=data_config["train_data_percentage"],
    )
    experiment_data = ExperimentData(
        name=type(curve_function).__name__.lower(),
        full=Data(x=x.double(), y=y.double(), name="full"),
        train=Data(x=x_train.double(), y=y_train.double(), name="train"),
        test=Data(x=x_test.double(), y=y_test.double(), name="test"),
        validation=Data(
            x=x_validation.double(), y=y_validation.double(), name="validation"
        ),
    )
    return experiment_data


def plot_experiment_data(
    experiment_data: ExperimentData,
    title: str,
    curve_name: str,
) -> None:
    fig, ax = plt.subplots(figsize=(13, 6.5))
    fig, ax = plot_1d_experiment_data(
        fig=fig,
        ax=ax,
        experiment_data=experiment_data,
    )
    ax.set_title(title)
    fig.tight_layout()
    if not os.path.isdir(f"experiments/curves/plots/{curve_name}"):
        os.makedirs(f"experiments/curves/plots/{curve_name}")
    plt.savefig(f"experiments/curves/plots/{curve_name}/experiment-data.png")
    plt.close()


def main(
    curve_function: Curve,
    data_config: Dict[str, Any],
    kernel_and_induce_data_config: Dict[str, Any],
    pwgf_config: Dict[str, Any],
    svgp_config: Dict[str, Any],
) -> None:
    experiment_data = get_experiment_data(
        data_config=data_config,
        curve_function=curve_function,
    )
    plot_experiment_data(
        experiment_data=experiment_data,
        title=f"{curve_function.__name__} data",
        curve_name=type(curve_function).__name__.lower(),
    )
    plot_curve_path = (
        f"experiments/curves/plots/{type(curve_function).__name__.lower()}"
    )
    results_curve_path = (
        f"experiments/curves/results/{type(curve_function).__name__.lower()}"
    )

    model, induce_data = optimise_kernel_and_induce_data(
        experiment_data=experiment_data,
        kernel=gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.RBFKernel(ard_num_dims=experiment_data.train.x.shape[1])
        ),
        induce_data_selector=ConditionalVarianceInduceDataSelector(),
        seed=kernel_and_induce_data_config["seed"],
        number_of_epochs=kernel_and_induce_data_config["number_of_epochs"],
        learning_rate=kernel_and_induce_data_config["learning_rate"],
        number_of_iterations=kernel_and_induce_data_config["number_of_iterations"],
        number_induce_points=int(
            kernel_and_induce_data_config["induce_data_factor"]
            * math.pow(
                experiment_data.train.x.shape[0],
                1 / kernel_and_induce_data_config["induce_data_power"],
            )
        ),
        batch_size=kernel_and_induce_data_config["batch_size"],
        gp_scheme=kernel_and_induce_data_config["gp_scheme"],
        plot_1d_iteration_path=plot_curve_path,
        plot_loss_path=plot_curve_path,
    )
    pwgf, particles = projected_wasserstein_gradient_flow(
        particle_name="exact-gp",
        kernel=model.kernel,
        experiment_data=experiment_data,
        induce_data=induce_data,
        number_of_particles=pwgf_config["number_of_particles"],
        number_of_epochs=pwgf_config["number_of_epochs"],
        learning_rate_upper=pwgf_config["learning_rate_upper"],
        learning_rate_lower=pwgf_config["learning_rate_lower"],
        number_of_learning_rate_searches=pwgf_config[
            "number_of_learning_rate_searches"
        ],
        max_particle_magnitude=pwgf_config["max_particle_magnitude"],
        observation_noise=model.likelihood.noise
        if kernel_and_induce_data_config["gp_scheme"] == "exact"
        else 1.0,
        jitter=pwgf_config["jitter"],
        seed=pwgf_config["seed"],
        plot_title=f"{type(curve_function).__name__}",
        plot_particles_path=plot_curve_path,
        plot_update_magnitude_path=plot_curve_path,
    )
    calculate_particle_metrics(
        model=pwgf,
        model_name="pwgf",
        dataset_name=type(curve_function).__name__,
        particles=particles,
        experiment_data=experiment_data,
        results_path=results_curve_path,
    )
    fixed_svgp_model = train_svgp(
        experiment_data=experiment_data,
        induce_data=induce_data,
        mean=gpytorch.means.ConstantMean(),
        kernel=model.kernel,
        seed=svgp_config["seed"],
        number_of_epochs=svgp_config["number_of_epochs"],
        batch_size=svgp_config["batch_size"],
        learning_rate_upper=svgp_config["learning_rate_upper"],
        learning_rate_lower=svgp_config["learning_rate_lower"],
        number_of_learning_rate_searches=svgp_config[
            "number_of_learning_rate_searches"
        ],
        is_fixed=True,
        plot_title=f"{type(curve_function).__name__}",
        plot_1d_path=plot_curve_path,
        plot_loss_path=plot_curve_path,
    )
    calculate_svgp_metrics(
        model=fixed_svgp_model,
        model_name="fixed-svgp",
        dataset_name=type(curve_function).__name__,
        experiment_data=experiment_data,
        results_path=results_curve_path,
    )
    svgp_model = train_svgp(
        experiment_data=experiment_data,
        induce_data=induce_data,
        mean=gpytorch.means.ConstantMean(),
        kernel=gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.RBFKernel(ard_num_dims=experiment_data.train.x.shape[1])
        ),
        seed=svgp_config["seed"],
        number_of_epochs=svgp_config["number_of_epochs"],
        batch_size=svgp_config["batch_size"],
        learning_rate_upper=svgp_config["learning_rate_upper"],
        learning_rate_lower=svgp_config["learning_rate_lower"],
        number_of_learning_rate_searches=svgp_config[
            "number_of_learning_rate_searches"
        ],
        is_fixed=False,
        plot_title=f"{type(curve_function).__name__}",
        plot_1d_path=plot_curve_path,
        plot_loss_path=plot_curve_path,
    )
    calculate_svgp_metrics(
        model=svgp_model,
        model_name="svgp",
        dataset_name=type(curve_function).__name__,
        experiment_data=experiment_data,
        results_path=results_curve_path,
    )
    svgp_pwgf, svgp_particles = projected_wasserstein_gradient_flow(
        particle_name="svgp",
        kernel=svgp_model.kernel,
        experiment_data=experiment_data,
        induce_data=Data(
            x=svgp_model.variational_strategy.inducing_points.detach(),
        ),
        number_of_particles=pwgf_config["number_of_particles"],
        number_of_epochs=pwgf_config["number_of_epochs"],
        learning_rate_upper=pwgf_config["learning_rate_upper"],
        learning_rate_lower=pwgf_config["learning_rate_lower"],
        number_of_learning_rate_searches=pwgf_config[
            "number_of_learning_rate_searches"
        ],
        max_particle_magnitude=pwgf_config["max_particle_magnitude"],
        observation_noise=model.likelihood.noise
        if kernel_and_induce_data_config["gp_scheme"] == "exact"
        else 1.0,
        jitter=pwgf_config["jitter"],
        seed=pwgf_config["seed"],
        plot_title=f"{type(curve_function).__name__} svGP kernel/induce data",
        plot_particles_path=plot_curve_path,
        plot_update_magnitude_path=plot_curve_path,
    )
    calculate_particle_metrics(
        model=svgp_pwgf,
        model_name="pwgf-svgp",
        dataset_name=type(curve_function).__name__,
        particles=svgp_particles,
        experiment_data=experiment_data,
        results_path=results_curve_path,
    )


if __name__ == "__main__":
    args = parser.parse_args()
    with open(args.config_path, "r") as file:
        loaded_config = yaml.safe_load(file)
    for curve_function_ in CURVE_FUNCTIONS:
        main(
            curve_function=curve_function_,
            data_config=loaded_config["data"],
            kernel_and_induce_data_config=loaded_config["kernel_and_induce_data"],
            pwgf_config=loaded_config["pwgf"],
            svgp_config=loaded_config["svgp"],
        )
    concatenate_metrics(
        results_path="experiments/curves/results",
        data_types=["train", "validation", "test"],
        models=["pwgf", "fixed-svgp", "svgp", "pwgf-svgp"],
        datasets=[
            type(curve_function_).__name__.lower()
            for curve_function_ in CURVE_FUNCTIONS
        ],
        metrics=["mae", "nll"],
    )