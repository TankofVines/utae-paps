"""
Script for semantic inference with pre-trained models
Author: Vivien Sainte Fare Garnot (github/VSainteuf)
License: MIT
"""
import argparse
import json
import os
import pprint

import numpy as np
import torch
import torch.nn as nn
import torch.utils.data as data
import wandb

from src import utils, model_utils
from src.dataset import PASTIS_Dataset

from train_semantic import iterate, overall_performance, save_results

parser = argparse.ArgumentParser()
# Model parameters
parser.add_argument(
    "--weight_folder",
    type=str,
    default="",
    help="Path to the main folder containing the pre-trained weights",
)
parser.add_argument(
    "--dataset_folder",
    default="",
    type=str,
    help="Path to the folder where the results are saved.",
)
parser.add_argument(
    "--num_workers", default=8, type=int, help="Number of data loading workers"
)
parser.add_argument(
    "--fold",
    default=None,
    type=int,
    help="Do only one of the five fold (between 1 and 5)",
)
parser.add_argument(
    "--device",
    default="cuda",
    type=str,
    help="Name of device to use for tensor computations (cuda/cpu)",
)
parser.add_argument(
    "--display_step",
    default=50,
    type=int,
    help="Interval in batches between display of training metrics",
)

def main(config):
    fold_sequence = [
        [[1, 2, 3], [4], [5]],
        [[2, 3, 4], [5], [1]],
        [[3, 4, 5], [1], [2]],
        [[4, 5, 1], [2], [3]],
        [[5, 1, 2], [3], [4]],
    ]

    with wandb.init(project="utae-pastis-semantic-wandb", job_type="load") as run:
        annotations_arti = wandb.Artifact('test_annotations', type='dataset')
        annotations_arti.add_dir('data/PASTIS/ANNOTATIONS')
        run.log_artifact(annotations_arti)

        image_arti = wandb.Artifact('test_images', type='dataset')
        image_arti.add_dir('data/PASTIS/DATA_S2')
        run.log_artifact(image_arti)

        run.finish()

    with wandb.init(project="utae-pastis-semantic-wandb", job_type="sample_table") as table_run:
        sample_test_tbl = wandb.Table(columns=["filename", "true_color", "false_color_ir", ])

        # Log the first n images in the test dataset
        for image in os.listdir(config.dataset_folder + "DATA_S2")[:10]:

            rgb_idx = [2, 1, 0]
            rgb = np.moveaxis(np.load(config.dataset_folder + "DATA_S2/" + image)[0, 1:4][rgb_idx], [0, 1, 2], [2, 0, 1])
            fci = np.moveaxis(np.load(config.dataset_folder + "DATA_S2/" + image)[0, [7, 3, 4]], [0, 1, 2], [2, 0, 1])

            sample_test_tbl.add_data(
                image,
                wandb.Image(rgb),
                wandb.Image(fci)
            )

        table_run.log({"sample-test-data": sample_test_tbl})

    np.random.seed(config.rdm_seed)
    torch.manual_seed(config.rdm_seed)
    device = torch.device(config.device)

    model = model_utils.get_model(config, mode="semantic")
    model = model.to(device)

    config.N_params = utils.get_ntrainparams(model)
    print(model)
    print("TOTAL TRAINABLE PARAMETERS :", config.N_params)

    fold_sequence = (
        fold_sequence if config.fold is None else [fold_sequence[config.fold - 1]]
    )
    for fold, (train_folds, val_fold, test_fold) in enumerate(fold_sequence):
        if config.fold is not None:
            fold = config.fold - 1

        # Dataset definition
        dt_test = PASTIS_Dataset(
            folder=config.dataset_folder,
            norm=True,
            reference_date=config.ref_date,
            mono_date=config.mono_date,
            target="semantic",
            sats=["S2"],
            folds=test_fold,
        )
        collate_fn = lambda x: utils.pad_collate(x, pad_value=config.pad_value)
        test_loader = data.DataLoader(
            dt_test,
            batch_size=config.batch_size,
            shuffle=True,
            drop_last=True,
            collate_fn=collate_fn,
        )

        # Load weights
        sd = torch.load(
            os.path.join(config.weight_folder, "Fold_{}".format(fold+1), "model.pth.tar"),
            map_location=device,
        )
        model.load_state_dict(sd["state_dict"])

        # Loss
        weights = torch.ones(config.num_classes, device=device).float()
        weights[config.ignore_index] = 0
        criterion = nn.CrossEntropyLoss(weight=weights)

        # Inference
        print("Testing . . .")
        model.load_state_dict(
            torch.load(
                os.path.join(
                    config.weight_folder, "Fold_{}".format(fold + 1), "model.pth.tar"
                )
            )["state_dict"]
        )
        model.eval()
        test_metrics, conf_mat = iterate(
            model,
            data_loader=test_loader,
            criterion=criterion,
            config=config,
            optimizer=None,
            mode="test",
            device=device,
        )
        print(
            "Loss {:.4f},  Acc {:.2f},  IoU {:.4f}".format(
                test_metrics["test_loss"],
                test_metrics["test_accuracy"],
                test_metrics["test_IoU"],
            )
        )
        save_results(fold + 1, test_metrics, conf_mat.cpu().numpy(), config)

    if config.fold is None:
        overall_performance(config)


if __name__ == "__main__":
    test_config = parser.parse_args()


    with open(os.path.join(test_config.weight_folder, "conf.json")) as file:
        model_config = json.loads(file.read())

    config = {**model_config, **vars(test_config)}
    config = argparse.Namespace(**config)
    config.fold = test_config.fold

    pprint.pprint(config)
    main(config)
