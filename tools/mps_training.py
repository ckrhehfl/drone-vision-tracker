"""Use blocking batch copies to avoid MPS CPU-source lifetime corruption."""

from copy import copy

import torch
from ultralytics.models.yolo.detect import DetectionTrainer, DetectionValidator


def blocking_batch(batch: dict, device) -> dict:
    return {
        key: value.to(device, non_blocking=False) if isinstance(value, torch.Tensor) else value
        for key, value in batch.items()
    }


class MPSDetectionValidator(DetectionValidator):
    def preprocess(self, batch):
        return super().preprocess(blocking_batch(batch, self.device))


class MPSDetectionTrainer(DetectionTrainer):
    def preprocess_batch(self, batch):
        return super().preprocess_batch(blocking_batch(batch, self.device))

    def get_validator(self):
        self.loss_names = "box_loss", "cls_loss", "dfl_loss"
        return MPSDetectionValidator(
            self.test_loader,
            save_dir=self.save_dir,
            args=copy(self.args),
            _callbacks=self.callbacks,
        )
