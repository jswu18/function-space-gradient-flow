import argparse
import math
import os
from copy import deepcopy
from typing import Any, Dict

import gpytorch
import pandas as pd
import torch
import yaml

from experiments.constructors import (
    construct_average_ard_kernel,
    construct_average_gaussian_likelihood,
)
from experiments.data import Data, ExperimentData
from experiments.loaders import load_projected_wasserstein_gradient_flow, load_svgp
from experiments.metrics import calculate_metrics, concatenate_metrics
from experiments.preprocess import set_up_experiment
from experiments.runners import (
    learn_subsample_gps,
    pwgf_observation_noise_search,
    select_induce_data,
    train_projected_wasserstein_gradient_flow,
    train_svgp,
)
from experiments.uci.constants import DATASET_SCHEMA_MAPPING
from experiments.uci.schemas import DatasetSchema
from src.induce_data_selectors import ConditionalVarianceInduceDataSelector
from src.utils import set_seed

parser = argparse.ArgumentParser(
    description="Main script for UCI regression data experiments."
)
parser.add_argument(
    "--config_path", type=str, required=True, help="Path to config for experiment."
)
parser.add_argument(
    "--dataset_name",
    type=str,
    required=True,
    help="Name of dataset to use for the experiment.",
)
parser.add_argument(
    "--data_seed",
    type=int,
    required=True,
    help="Seed to use for the data split of the experiment.",
)


def get_experiment_data(
    seed: int,
    train_data_percentage: float,
    validation_data_percentage: float,
    test_data_percentage: float,
    dataset_name: str,
) -> ExperimentData:
    df = pd.read_csv(
        os.path.join("experiments", "uci", "datasets", f"{dataset_name}.csv")
    )
    df.columns = [c.lower() for c in df.columns]
    df.columns = [c.replace(" ", "") for c in df.columns]
    dataset_metadata = DATASET_SCHEMA_MAPPING[dataset_name]
    input_column_names = [c.lower() for c in dataset_metadata.input_column_names]
    input_column_names = [c.replace(" ", "") for c in input_column_names]
    output_column_name = dataset_metadata.output_column_name.lower().replace(" ", "")

    x = torch.tensor(df[input_column_names].to_numpy()).double().detach()
    y = torch.tensor(df[output_column_name].to_numpy()).double().detach()

    experiment_data = set_up_experiment(
        name=dataset_name,
        seed=seed,
        x=x,
        y=y,
        train_data_percentage=train_data_percentage,
        validation_data_percentage=validation_data_percentage,
        test_data_percentage=test_data_percentage,
        normalise=True,
    )
    return experiment_data


def main(
    dataset_name: str,
    data_seed: int,
    data_config: Dict[str, Any],
    kernel_config: Dict[str, Any],
    induce_data_config: Dict[str, Any],
    pwgf_config: Dict[str, Any],
    svgp_config: Dict[str, Any],
) -> None:
    print(f"Running experiment for {dataset_name=} and {data_seed=}.")

    data_path = f"experiments/uci/outputs/{data_seed}/data/{dataset_name}"
    plots_path = f"experiments/uci/outputs/{data_seed}/plots/{dataset_name}"
    results_path = f"experiments/uci/outputs/{data_seed}/results/{dataset_name}"
    models_path = f"experiments/uci/outputs/{data_seed}/models/{dataset_name}"
    subsample_gp_model_path = os.path.join(models_path, "subsample_gp")
    subsample_gp_data_path = os.path.join(data_path, "subsample_gp")
    fixed_svgp_iteration_model_path = os.path.join(
        models_path, "fixed_svgp_model_iterations"
    )
    svgp_iteration_model_path = os.path.join(models_path, "svgp_model_iterations")
    os.makedirs(data_path, exist_ok=True)
    os.makedirs(plots_path, exist_ok=True)
    os.makedirs(results_path, exist_ok=True)
    os.makedirs(models_path, exist_ok=True)

    experiment_data_path = os.path.join(data_path, "experiment_data.pth")
    induce_data_path = os.path.join(data_path, "inducing_points.pth")
    pwgf_path = os.path.join(models_path, "pwgf_model.pth")
    fixed_svgp_model_path = os.path.join(models_path, "fixed_svgp_model.pth")
    svgp_model_path = os.path.join(models_path, "svgp_model.pth")
    svgp_pwgf_path = os.path.join(models_path, "svgp_pwgf_model.pth")

    if os.path.exists(experiment_data_path):
        experiment_data = ExperimentData.load(experiment_data_path)
        print(f"Loaded experiment data from {experiment_data_path=}")
    else:
        experiment_data = get_experiment_data(
            seed=data_seed,
            train_data_percentage=data_config["train_data_percentage"],
            validation_data_percentage=data_config["validation_data_percentage"],
            test_data_percentage=data_config["test_data_percentage"],
            dataset_name=dataset_name,
        )
        experiment_data.save(experiment_data_path)

    subsample_gp_models = learn_subsample_gps(
        experiment_data=experiment_data,
        kernel=gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.RBFKernel(ard_num_dims=experiment_data.train.x.shape[1])
        ),
        subsample_size=kernel_config["subsample_size"],
        seed=kernel_config["seed"],
        number_of_epochs=kernel_config["number_of_epochs"],
        learning_rate=kernel_config["learning_rate"],
        number_of_iterations=kernel_config["number_of_iterations"],
        plot_1d_subsample_path=None,
        plot_loss_path=plots_path,
        model_path=subsample_gp_model_path,
        data_path=subsample_gp_data_path,
    )
    average_ard_kernel = construct_average_ard_kernel(
        kernels=[model.kernel for model in subsample_gp_models]
    )
    if os.path.exists(induce_data_path):
        induce_data = torch.load(induce_data_path)
    else:
        induce_data = select_induce_data(
            seed=induce_data_config["seed"],
            induce_data_selector=ConditionalVarianceInduceDataSelector(),
            data=experiment_data.train,
            number_induce_points=int(
                induce_data_config["induce_data_factor"]
                * math.pow(
                    experiment_data.train.x.shape[0],
                    1 / induce_data_config["induce_data_power"],
                )
            ),
            kernel=average_ard_kernel,
        )
        torch.save(induce_data, induce_data_path)
    likelihood = construct_average_gaussian_likelihood(
        likelihoods=[model.likelihood for model in subsample_gp_models]
    )
    if os.path.exists(pwgf_path):
        pwgf = load_projected_wasserstein_gradient_flow(
            model_path=pwgf_path,
            base_kernel=deepcopy(average_ard_kernel),
            observation_noise=float(likelihood.noise),
            experiment_data=experiment_data,
            induce_data=induce_data,
            jitter=pwgf_config["jitter"],
        )
    else:
        pwgf = train_projected_wasserstein_gradient_flow(
            particle_name="average-kernel",
            kernel=deepcopy(average_ard_kernel),
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
            observation_noise=float(likelihood.noise),
            jitter=pwgf_config["jitter"],
            seed=pwgf_config["seed"],
            plot_title=f"{dataset_name}",
            plot_particles_path=None,
            plot_update_magnitude_path=plots_path,
        )
        pwgf.observation_noise = pwgf_observation_noise_search(
            data=experiment_data.train,
            model=pwgf,
            observation_noise_upper=pwgf_config["observation_noise_upper"],
            observation_noise_lower=pwgf_config["observation_noise_lower"],
            number_of_searches=pwgf_config["number_of_observation_noise_searches"],
            y_std=experiment_data.y_std,
        )
        torch.save(
            {
                "particles": pwgf.particles,
                "observation_noise": pwgf.observation_noise,
            },
            pwgf_path,
        )
    set_seed(pwgf_config["seed"])
    calculate_metrics(
        model=pwgf,
        model_name="pwgf",
        dataset_name=dataset_name,
        experiment_data=experiment_data,
        results_path=results_path,
        plots_path=plots_path,
    )
    if os.path.exists(fixed_svgp_model_path):
        fixed_svgp_model, _ = load_svgp(
            model_path=fixed_svgp_model_path,
            x_induce=induce_data.x,
            mean=gpytorch.means.ConstantMean(),
            kernel=deepcopy(average_ard_kernel),
            learn_inducing_locations=False,
        )
    else:
        fixed_svgp_model, losses = train_svgp(
            experiment_data=experiment_data,
            induce_data=induce_data,
            mean=gpytorch.means.ConstantMean(),
            kernel=deepcopy(average_ard_kernel),
            seed=svgp_config["seed"],
            number_of_epochs=svgp_config["number_of_epochs"],
            batch_size=svgp_config["batch_size"],
            learning_rate_upper=svgp_config["learning_rate_upper"],
            learning_rate_lower=svgp_config["learning_rate_lower"],
            number_of_learning_rate_searches=svgp_config[
                "number_of_learning_rate_searches"
            ],
            is_fixed=True,
            observation_noise=pwgf.observation_noise,
            models_path=fixed_svgp_iteration_model_path,
            plot_title=f"{dataset_name}",
            plot_1d_path=None,
            plot_loss_path=plots_path,
        )
        torch.save(
            {
                "model": fixed_svgp_model.state_dict(),
                "losses": losses,
            },
            fixed_svgp_model_path,
        )
    set_seed(svgp_config["seed"])
    calculate_metrics(
        model=fixed_svgp_model,
        model_name="fixed-svgp",
        dataset_name=dataset_name,
        experiment_data=experiment_data,
        results_path=results_path,
        plots_path=plots_path,
    )
    if os.path.exists(svgp_model_path):
        svgp_model, _ = load_svgp(
            model_path=svgp_model_path,
            x_induce=induce_data.x,
            mean=gpytorch.means.ConstantMean(),
            kernel=gpytorch.kernels.ScaleKernel(
                gpytorch.kernels.RBFKernel(
                    ard_num_dims=experiment_data.train.x.shape[1]
                )
            ),
            learn_inducing_locations=True,
        )
    else:
        svgp_model, losses = train_svgp(
            experiment_data=experiment_data,
            induce_data=induce_data,
            mean=gpytorch.means.ConstantMean(),
            kernel=gpytorch.kernels.ScaleKernel(
                gpytorch.kernels.RBFKernel(
                    ard_num_dims=experiment_data.train.x.shape[1]
                )
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
            models_path=svgp_iteration_model_path,
            plot_title=f"{dataset_name}",
            plot_1d_path=None,
            plot_loss_path=plots_path,
        )
        torch.save(
            {
                "model": svgp_model.state_dict(),
                "losses": losses,
            },
            svgp_model_path,
        )
    set_seed(svgp_config["seed"])
    calculate_metrics(
        model=svgp_model,
        model_name="svgp",
        dataset_name=dataset_name,
        experiment_data=experiment_data,
        results_path=results_path,
        plots_path=plots_path,
    )

    if os.path.exists(svgp_pwgf_path):
        svgp_pwgf = load_projected_wasserstein_gradient_flow(
            model_path=svgp_pwgf_path,
            base_kernel=deepcopy(svgp_model.kernel),
            observation_noise=svgp_model.likelihood.noise,
            experiment_data=experiment_data,
            induce_data=Data(
                x=deepcopy(svgp_model.variational_strategy.inducing_points),
                y=None,
            ),
            jitter=pwgf_config["jitter"],
        )
    else:
        svgp_pwgf = train_projected_wasserstein_gradient_flow(
            particle_name="svgp",
            kernel=deepcopy(svgp_model.kernel),
            experiment_data=experiment_data,
            induce_data=Data(
                x=deepcopy(svgp_model.variational_strategy.inducing_points),
                y=None,
            ),
            number_of_particles=pwgf_config["number_of_particles"],
            number_of_epochs=pwgf_config["number_of_epochs"],
            learning_rate_upper=pwgf_config["learning_rate_upper"],
            learning_rate_lower=pwgf_config["learning_rate_lower"],
            number_of_learning_rate_searches=pwgf_config[
                "number_of_learning_rate_searches"
            ],
            max_particle_magnitude=pwgf_config["max_particle_magnitude"],
            observation_noise=svgp_model.likelihood.noise,
            jitter=pwgf_config["jitter"],
            seed=pwgf_config["seed"],
            plot_title=f"{dataset_name} svGP kernel/induce data",
            plot_particles_path=None,
            plot_update_magnitude_path=plots_path,
        )
        torch.save(
            {
                "particles": svgp_pwgf.particles,
                "observation_noise": svgp_pwgf.observation_noise,
            },
            svgp_pwgf_path,
        )
    set_seed(pwgf_config["seed"])
    calculate_metrics(
        model=svgp_pwgf,
        model_name="pwgf-svgp",
        dataset_name=dataset_name,
        experiment_data=experiment_data,
        results_path=results_path,
        plots_path=plots_path,
    )


if __name__ == "__main__":
    args = parser.parse_args()
    with open(args.config_path, "r") as file:
        loaded_config = yaml.safe_load(file)
    main(
        dataset_name=args.dataset_name,
        data_seed=args.data_seed,
        data_config=loaded_config["data"],
        kernel_config=loaded_config["kernel"],
        induce_data_config=loaded_config["induce_data"],
        pwgf_config=loaded_config["pwgf"],
        svgp_config=loaded_config["svgp"],
    )
    concatenate_metrics(
        results_path=f"experiments/uci/outputs/{args.data_seed}/results",
        data_types=["train", "validation", "test"],
        model_names=["pwgf", "fixed-svgp", "svgp", "pwgf-svgp"],
        datasets=list(DatasetSchema.__members__.keys()),
        metrics=["mae", "mse", "nll", "average_interval_width"],
    )
