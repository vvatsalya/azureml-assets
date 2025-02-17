# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""PyFunc MLflow model convertors."""

import json
import mlflow
import os
import sys

from abc import ABC, abstractmethod
from mlflow.models.signature import ModelSignature
from mlflow.pyfunc import PyFuncModel
from mlflow.types import DataType
from mlflow.types.schema import ColSpec, Schema
from pathlib import Path
from typing import Dict, List, Optional

from azureml.model.mgmt.utils.common_utils import fetch_mlflow_acft_metadata
from azureml.model.mgmt.utils.logging_utils import get_logger
from azureml.model.mgmt.processors.convertors import MLFLowConvertorInterface
from azureml.model.mgmt.processors.pyfunc.config import (
    MMLabDetectionTasks, MMLabTrackingTasks, SupportedTasks)

from azureml.model.mgmt.processors.pyfunc.clip.config import \
    MLflowSchemaLiterals as CLIPMLFlowSchemaLiterals, MLflowLiterals as CLIPMLflowLiterals
from azureml.model.mgmt.processors.pyfunc.blip.config import \
    MLflowSchemaLiterals as BLIPMLFlowSchemaLiterals, MLflowLiterals as BLIPMLflowLiterals
from azureml.model.mgmt.processors.pyfunc.text_to_image.config import (
    MLflowSchemaLiterals as TextToImageMLFlowSchemaLiterals,
    MLflowLiterals as TextToImageMLflowLiterals,
)
from azureml.model.mgmt.processors.pyfunc.llava.config import \
    MLflowSchemaLiterals as LLaVAMLFlowSchemaLiterals, MLflowLiterals as LLaVAMLflowLiterals
from azureml.model.mgmt.processors.pyfunc.vision.config import \
    MLflowSchemaLiterals as VisionMLFlowSchemaLiterals, MMDetLiterals


logger = get_logger(__name__)


class PyFuncMLFLowConvertor(MLFLowConvertorInterface, ABC):
    """PyFunc MLflow convertor base class."""

    CONDA_FILE_NAME = "conda.yaml"
    REQUIREMENTS_FILE_NAME = "requirements.txt"
    COMMON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "common")
    sys.path.append(COMMON_DIR)

    @abstractmethod
    def get_model_signature(self) -> ModelSignature:
        """Return model signature for MLflow model.

        :return: MLflow model signature.
        :rtype: mlflow.models.signature.ModelSignature
        """
        raise NotImplementedError

    @abstractmethod
    def save_as_mlflow(self):
        """Prepare model for save to MLflow."""
        raise NotImplementedError

    def __init__(
        self,
        model_dir: Path,
        output_dir: Path,
        temp_dir: Path,
        translate_params: Dict,
    ):
        """Initialize MLflow convertor for PyFunc models."""
        self._validate(translate_params)
        self._model_dir = os.fspath(model_dir)
        self._output_dir = output_dir
        self._temp_dir = temp_dir
        self._model_id = translate_params.get("model_id")
        self._task = translate_params["task"]
        self._signatures = translate_params.get("signatures", None)

    def _save(
        self,
        mlflow_model_wrapper: PyFuncModel,
        artifacts_dict: Dict[str, str],
        code_path: List[str],
        pip_requirements: Optional[str] = None,
        conda_env: Optional[str] = None,
    ):
        """Save Mlflow model to output directory.

        :param mlflow_model_wrapper: MLflow model wrapper instance
        :type mlflow_model_wrapper: Subclass of PyFuncModel
        :param artifacts_dict: Dictionary of name to artifact path
        :type artifacts_dict: Dict[str, str]
        :param pip_requirements: Path to pip requirements file
        :type pip_requirements: Optional[str]
        :param conda_env: Path to conda environment yaml file
        :type conda_env: Optional[str]
        :param code_path: A list of local filesystem paths to Python file dependencies
        :type code_path: List[str]

        """
        signatures = self._signatures or self.get_model_signature()
        # set metadata info
        metadata = fetch_mlflow_acft_metadata(base_model_name=self._model_id,
                                              is_finetuned_model=False,
                                              base_model_task=self._task)
        mlflow.pyfunc.save_model(
            path=self._output_dir,
            python_model=mlflow_model_wrapper,
            artifacts=artifacts_dict,
            pip_requirements=pip_requirements,
            conda_env=conda_env,
            signature=signatures,
            code_path=code_path,
            metadata=metadata,
        )

        logger.info("Model saved successfully.")

    def _validate(self, translate_params):
        """Validate translate parameters."""
        if not translate_params.get("task"):
            raise Exception("task is a required parameter for pyfunc flavor.")
        task = translate_params["task"]
        if not SupportedTasks.has_value(task):
            raise Exception(f"Unsupported task {task} for pyfunc flavor.")


class MMLabDetectionMLflowConvertor(PyFuncMLFLowConvertor):
    """PyFunc MLfLow convertor for detection models from MMLab."""

    MODEL_DIR = os.path.join(os.path.dirname(__file__), "vision")
    COMMON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "common")

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for vision models."""
        super().__init__(**kwargs)
        if not MMLabDetectionTasks.has_value(self._task):
            raise Exception("Unsupported vision task")

    def get_model_signature(self) -> ModelSignature:
        """Return MLflow model signature with input and output schema for the given input task.

        :return: MLflow model signature.
        :rtype: mlflow.models.signature.ModelSignature
        """
        input_schema = Schema(
            [
                ColSpec(VisionMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE_DATA_TYPE,
                        VisionMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE)
            ]
        )

        if self._task in [MMLabDetectionTasks.MM_OBJECT_DETECTION.value,
                          MMLabDetectionTasks.MM_INSTANCE_SEGMENTATION.value]:
            output_schema = Schema(
                [
                    ColSpec(VisionMLFlowSchemaLiterals.OUTPUT_COLUMN_DATA_TYPE,
                            VisionMLFlowSchemaLiterals.OUTPUT_COLUMN_BOXES),
                ]
            )
        else:
            raise NotImplementedError(f"Task type: {self._task} is not supported yet.")
        return ModelSignature(inputs=input_schema, outputs=output_schema)

    def save_as_mlflow(self):
        """Prepare model for save to MLflow."""
        sys.path.append(self.MODEL_DIR)
        from detection_predict import ImagesDetectionMLflowModelWrapper

        mlflow_model_wrapper = ImagesDetectionMLflowModelWrapper(task_type=self._task)
        artifacts_dict = self._prepare_artifacts_dict()
        if self._task == MMLabDetectionTasks.MM_OBJECT_DETECTION.value:
            pip_requirements = os.path.join(self.MODEL_DIR, "mmdet-od-requirements.txt")
        elif self._task == MMLabDetectionTasks.MM_INSTANCE_SEGMENTATION.value:
            pip_requirements = os.path.join(self.MODEL_DIR, "mmdet-is-requirements.txt")
        else:
            pip_requirements = None
        code_path = [
            os.path.join(self.MODEL_DIR, "detection_predict.py"),
            os.path.join(self.MODEL_DIR, "config.py"),
            os.path.join(self.COMMON_DIR, "vision_utils.py")
        ]
        super()._save(
            mlflow_model_wrapper=mlflow_model_wrapper,
            artifacts_dict=artifacts_dict,
            pip_requirements=pip_requirements,
            code_path=code_path,
        )

    def _prepare_artifacts_dict(self) -> Dict:
        """Prepare artifacts dict for MLflow model.

        :return: artifacts dict
        :rtype: Dict
        """
        metadata_path = os.path.join(self._model_dir, "model_selector_args.json")
        with open(metadata_path) as f:
            metadata = json.load(f)

        artifacts_dict = {
            MMDetLiterals.CONFIG_PATH: os.path.join(self._model_dir, metadata.get("pytorch_model_path")),
            MMDetLiterals.WEIGHTS_PATH: os.path.join(self._model_dir, metadata.get("model_weights_path_or_url")),
            MMDetLiterals.METAFILE_PATH: os.path.join(self._model_dir, metadata.get("model_metafile_path")),
        }
        return artifacts_dict


class CLIPMLFlowConvertor(PyFuncMLFLowConvertor):
    """PyFunc MLfLow convertor for CLIP models."""

    MODEL_DIR = os.path.join(os.path.dirname(__file__), "clip")
    COMMON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "common")

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for CLIP models."""
        super().__init__(**kwargs)
        if self._task not in \
                [SupportedTasks.ZERO_SHOT_IMAGE_CLASSIFICATION.value, SupportedTasks.EMBEDDINGS.value]:
            raise Exception("Unsupported task")

    def get_model_signature(self) -> ModelSignature:
        """Return MLflow model signature with input and output schema for the given input task.

        :return: MLflow model signature.
        :rtype: mlflow.models.signature.ModelSignature
        """
        input_schema = Schema(
            [
                ColSpec(CLIPMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE_DATA_TYPE,
                        CLIPMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE),
                ColSpec(CLIPMLFlowSchemaLiterals.INPUT_COLUMN_TEXT_DATA_TYPE,
                        CLIPMLFlowSchemaLiterals.INPUT_COLUMN_TEXT),
            ]
        )

        if self._task == SupportedTasks.ZERO_SHOT_IMAGE_CLASSIFICATION.value:
            output_schema = Schema(
                [
                    ColSpec(CLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_DATA_TYPE,
                            CLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_PROBS),
                    ColSpec(CLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_DATA_TYPE,
                            CLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_LABELS),
                ]
            )
        elif self._task == SupportedTasks.EMBEDDINGS.value:
            output_schema = Schema(
                [
                    ColSpec(CLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_DATA_TYPE,
                            CLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_IMAGE_FEATURES),
                    ColSpec(CLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_DATA_TYPE,
                            CLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_TEXT_FEATURES),
                ]
            )
        else:
            raise Exception("Unsupported task")

        return ModelSignature(inputs=input_schema, outputs=output_schema)

    def save_as_mlflow(self):
        """Prepare model for save to MLflow."""
        sys.path.append(self.MODEL_DIR)

        if self._task == SupportedTasks.ZERO_SHOT_IMAGE_CLASSIFICATION.value:
            from clip_mlflow_wrapper import CLIPMLFlowModelWrapper
            mlflow_model_wrapper = CLIPMLFlowModelWrapper(task_type=self._task)
        elif self._task == SupportedTasks.EMBEDDINGS.value:
            from clip_embeddings_mlflow_wrapper import CLIPEmbeddingsMLFlowModelWrapper
            mlflow_model_wrapper = CLIPEmbeddingsMLFlowModelWrapper(task_type=self._task)
        else:
            raise Exception("Unsupported task")

        artifacts_dict = self._prepare_artifacts_dict()
        conda_env_file = os.path.join(self.MODEL_DIR, "conda.yaml")
        code_path = self._get_code_path()

        super()._save(
            mlflow_model_wrapper=mlflow_model_wrapper,
            artifacts_dict=artifacts_dict,
            conda_env=conda_env_file,
            code_path=code_path,
        )

    def _get_code_path(self):
        """Return code path for saving mlflow model depending on task type.

        :return: code path
        :rtype: List[str]
        """
        code_path = [
            os.path.join(self.MODEL_DIR, "clip_mlflow_wrapper.py"),
            os.path.join(self.MODEL_DIR, "config.py"),
            os.path.join(self.COMMON_DIR, "vision_utils.py")
        ]
        if self._task == SupportedTasks.EMBEDDINGS.value:
            code_path.append(os.path.join(self.MODEL_DIR, "clip_embeddings_mlflow_wrapper.py"))

        return code_path

    def _prepare_artifacts_dict(self) -> Dict:
        """Prepare artifacts dict for MLflow model.

        :return: artifacts dict
        :rtype: Dict
        """
        artifacts_dict = {
            CLIPMLflowLiterals.MODEL_DIR: self._model_dir
        }
        return artifacts_dict


class BLIPMLFlowConvertor(PyFuncMLFLowConvertor):
    """PyFunc MLfLow convertor for BLIP models."""

    MODEL_DIR = os.path.join(os.path.dirname(__file__), "blip")
    COMMON_DIR = os.path.join(os.path.dirname(
        os.path.dirname(__file__)), "common")

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for BLIP models."""
        super().__init__(**kwargs)
        if self._task not in [SupportedTasks.IMAGE_TO_TEXT.value, SupportedTasks.VISUAL_QUESTION_ANSWERING.value]:
            raise Exception("Unsupported task")

    def get_model_signature(self) -> ModelSignature:
        """Return MLflow model signature with input and output schema for the given input task.

        :return: MLflow model signature.
        :rtype: mlflow.models.signature.ModelSignature
        """
        if self._task == SupportedTasks.IMAGE_TO_TEXT.value:
            input_schema = Schema(
                [
                    ColSpec(BLIPMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE_DATA_TYPE,
                            BLIPMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE),
                ]
            )
        elif self._task == SupportedTasks.VISUAL_QUESTION_ANSWERING.value:
            input_schema = Schema(
                [
                    ColSpec(BLIPMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE_DATA_TYPE,
                            BLIPMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE),
                    ColSpec(BLIPMLFlowSchemaLiterals.INPUT_COLUMN_TEXT_DATA_TYPE,
                            BLIPMLFlowSchemaLiterals.INPUT_COLUMN_TEXT),
                ]
            )
        else:
            raise Exception("Unsupported task")

        output_schema = Schema(
            [
                ColSpec(BLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_DATA_TYPE,
                        BLIPMLFlowSchemaLiterals.OUTPUT_COLUMN_TEXT),
            ]
        )

        return ModelSignature(inputs=input_schema, outputs=output_schema)

    def save_as_mlflow(self):
        """Prepare model for save to MLflow."""
        sys.path.append(self.MODEL_DIR)
        from mlflow_wrapper import BLIPMLFlowModelWrapper

        mlflow_model_wrapper = BLIPMLFlowModelWrapper(task_type=self._task, model_id=self._model_id)
        artifacts_dict = {
            BLIPMLflowLiterals.MODEL_DIR: self._model_dir
        }
        conda_env_file = os.path.join(self.MODEL_DIR, "conda.yaml")
        code_path = [
            os.path.join(self.MODEL_DIR, "mlflow_wrapper.py"),
            os.path.join(self.MODEL_DIR, "config.py"),
            os.path.join(self.COMMON_DIR, "vision_utils.py"),
        ]
        super()._save(
            mlflow_model_wrapper=mlflow_model_wrapper,
            artifacts_dict=artifacts_dict,
            conda_env=conda_env_file,
            code_path=code_path,
        )


class TextToImageMLflowConvertor(PyFuncMLFLowConvertor):
    """MlfLow convertor base class for text to image models."""

    MODEL_DIR = os.path.join(os.path.dirname(__file__), "text_to_image")

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for text to image models."""
        super().__init__(**kwargs)

    def get_model_signature(self):
        """Return model signature for text to image models."""
        input_schema = Schema(inputs=[
            ColSpec(name=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_PROMPT,
                    type=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_PROMPT_DATA_TYPE,)
        ])
        output_schema = Schema(inputs=[
            ColSpec(name=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_PROMPT,
                    type=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_PROMPT_DATA_TYPE,),
            ColSpec(name=TextToImageMLFlowSchemaLiterals.OUTPUT_COLUMN_IMAGE,
                    type=TextToImageMLFlowSchemaLiterals.OUTPUT_COLUMN_IMAGE_TYPE),
            ColSpec(name=TextToImageMLFlowSchemaLiterals.OUTPUT_COLUMN_NSFW_FLAG,
                    type=TextToImageMLFlowSchemaLiterals.OUTPUT_COLUMN_NSFW_FLAG_TYPE,),
        ])
        return ModelSignature(inputs=input_schema, outputs=output_schema)


class StableDiffusionMlflowConvertor(TextToImageMLflowConvertor):
    """HF MlfLow convertor class for stable diffusion models."""

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for SD models."""
        super().__init__(**kwargs)

    def _prepare_artifacts_dict(self) -> Dict:
        """Prepare artifacts dict for MLflow model.

        :return: artifacts dict
        :rtype: Dict
        """
        artifacts_dict = {
            TextToImageMLflowLiterals.MODEL_DIR: self._model_dir
        }
        return artifacts_dict

    def save_as_mlflow(self):
        """Prepare SD model for save to MLflow."""
        """Prepare model for save to MLflow."""
        sys.path.append(self.MODEL_DIR)
        from stable_diffusion_mlflow_wrapper import StableDiffusionMLflowWrapper

        mlflow_model_wrapper = StableDiffusionMLflowWrapper(task_type=self._task)
        artifacts_dict = self._prepare_artifacts_dict()
        conda_env_file = os.path.join(self.MODEL_DIR, "conda.yaml")
        code_path = [
            os.path.join(self.MODEL_DIR, "stable_diffusion_mlflow_wrapper.py"),
            os.path.join(self.MODEL_DIR, "config.py"),
            os.path.join(self.COMMON_DIR, "vision_utils.py")
        ]
        super()._save(
            mlflow_model_wrapper=mlflow_model_wrapper,
            artifacts_dict=artifacts_dict,
            conda_env=conda_env_file,
            code_path=code_path,
        )


class TextToImageInpaintingMLflowConvertor(PyFuncMLFLowConvertor):
    """MlfLow convertor base class for text to image inpainting models."""

    MODEL_DIR = os.path.join(os.path.dirname(__file__), "text_to_image")

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for text to image models."""
        super().__init__(**kwargs)

    def get_model_signature(self):
        """Return model signature for text to image models."""
        input_schema = Schema(inputs=[
            ColSpec(name=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_PROMPT,
                    type=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_PROMPT_DATA_TYPE,),
            ColSpec(name=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE,
                    type=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE_TYPE,),
            ColSpec(name=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_MASK_IMAGE,
                    type=TextToImageMLFlowSchemaLiterals.INPUT_COLUMN_MASK_IMAGE_TYPE,)
        ])
        output_schema = Schema(inputs=[
            ColSpec(name=TextToImageMLFlowSchemaLiterals.OUTPUT_COLUMN_IMAGE,
                    type=TextToImageMLFlowSchemaLiterals.OUTPUT_COLUMN_IMAGE_TYPE),
            ColSpec(
                name=TextToImageMLFlowSchemaLiterals.OUTPUT_COLUMN_NSFW_FLAG,
                type=TextToImageMLFlowSchemaLiterals.OUTPUT_COLUMN_NSFW_FLAG_TYPE,
            ),
        ])
        return ModelSignature(inputs=input_schema, outputs=output_schema)


class StableDiffusionInpaintingMlflowConvertor(TextToImageInpaintingMLflowConvertor):
    """HF MlfLow convertor class for stable diffusion inpainting models."""

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for SD inpainting models."""
        super().__init__(**kwargs)

    def _prepare_artifacts_dict(self) -> Dict:
        """Prepare artifacts dict for MLflow model.

        :return: artifacts dict
        :rtype: Dict
        """
        artifacts_dict = {
            TextToImageMLflowLiterals.MODEL_DIR: self._model_dir
        }
        return artifacts_dict

    def save_as_mlflow(self):
        """Prepare SD model for save to MLflow."""
        """Prepare model for save to MLflow."""
        sys.path.append(self.MODEL_DIR)
        from stable_diffusion_inpainting_mlflow_wrapper import StableDiffusionInpaintingMLflowWrapper

        mlflow_model_wrapper = StableDiffusionInpaintingMLflowWrapper(task_type=self._task)
        artifacts_dict = self._prepare_artifacts_dict()
        conda_env_file = os.path.join(self.MODEL_DIR, "conda.yaml")
        code_path = [
            os.path.join(self.MODEL_DIR, "stable_diffusion_inpainting_mlflow_wrapper.py"),
            os.path.join(self.MODEL_DIR, "config.py"),
            os.path.join(self.COMMON_DIR, "vision_utils.py")
        ]
        super()._save(
            mlflow_model_wrapper=mlflow_model_wrapper,
            artifacts_dict=artifacts_dict,
            conda_env=conda_env_file,
            code_path=code_path,
        )


class LLaVAMLFlowConvertor(PyFuncMLFLowConvertor):
    """PyFunc MLfLow convertor for LLaVA models."""

    MODEL_DIR = os.path.join(os.path.dirname(__file__), "llava")
    COMMON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "common")

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for LLaVA models."""
        super().__init__(**kwargs)
        if self._task != SupportedTasks.IMAGE_TEXT_TO_TEXT.value:
            raise Exception("Unsupported task")

    def get_model_signature(self) -> ModelSignature:
        """Return MLflow model signature with input and output schema for the given input task.

        :return: MLflow model signature.
        :rtype: mlflow.models.signature.ModelSignature
        """
        input_schema = Schema(
            [
                ColSpec(DataType.string, LLaVAMLFlowSchemaLiterals.INPUT_COLUMN_IMAGE),
                ColSpec(DataType.string, LLaVAMLFlowSchemaLiterals.INPUT_COLUMN_PROMPT),
                ColSpec(DataType.string, LLaVAMLFlowSchemaLiterals.INPUT_COLUMN_DIRECT_QUESTION),
            ]
        )

        output_schema = Schema(
            [
                ColSpec(DataType.string, LLaVAMLFlowSchemaLiterals.OUTPUT_COLUMN_RESPONSE)
            ]
        )

        return ModelSignature(inputs=input_schema, outputs=output_schema)

    def save_as_mlflow(self):
        """Prepare model for save to MLflow."""
        sys.path.append(self.MODEL_DIR)
        from llava_mlflow_wrapper import LLaVAMLflowWrapper

        mlflow_model_wrapper = LLaVAMLflowWrapper(task_type=self._task)
        artifacts_dict = self._prepare_artifacts_dict()
        conda_env_file = os.path.join(self.MODEL_DIR, "conda.yaml")
        code_path = [
            os.path.join(self.MODEL_DIR, "llava_mlflow_wrapper.py"),
            os.path.join(self.MODEL_DIR, "config.py"),
            os.path.join(self.COMMON_DIR, "vision_utils.py")
        ]
        super()._save(
            mlflow_model_wrapper=mlflow_model_wrapper,
            artifacts_dict=artifacts_dict,
            conda_env=conda_env_file,
            code_path=code_path,
        )

    def _prepare_artifacts_dict(self) -> Dict:
        """Prepare artifacts dict for MLflow model.

        :return: artifacts dict
        :rtype: Dict
        """
        # Get the name of the only subdirectory of the model directory.
        sd = next(
            (d for d in os.listdir(self._model_dir) if os.path.isdir(os.path.join(self._model_dir, d))),
            self._model_dir
        )

        # Set model_dir parameter to point to subdirectory.
        artifacts_dict = {
            LLaVAMLflowLiterals.MODEL_DIR: os.path.join(self._model_dir, sd)
        }
        return artifacts_dict


class MMLabTrackingMLflowConvertor(PyFuncMLFLowConvertor):
    """PyFunc MLfLow convertor for tracking models from MMLab."""

    MODEL_DIR = os.path.join(os.path.dirname(__file__), "vision")
    COMMON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "common")

    def __init__(self, **kwargs):
        """Initialize MLflow convertor for vision models."""
        super().__init__(**kwargs)
        if not MMLabTrackingTasks.has_value(self._task):
            raise Exception("Unsupported vision task")

    def get_model_signature(self) -> ModelSignature:
        """Return MLflow model signature with input and output schema for the given input task.

        :return: MLflow model signature.
        :rtype: mlflow.models.signature.ModelSignature
        """
        input_schema = Schema(
            [
                ColSpec(VisionMLFlowSchemaLiterals.INPUT_COLUMN_VIDEO_DATA_TYPE,
                        VisionMLFlowSchemaLiterals.INPUT_COLUMN_VIDEO)
            ]
        )

        if self._task in [MMLabTrackingTasks.MM_MULTI_OBJECT_TRACKING.value]:
            output_schema = Schema(
                [
                    ColSpec(VisionMLFlowSchemaLiterals.OUTPUT_COLUMN_DATA_TYPE,
                            VisionMLFlowSchemaLiterals.OUTPUT_COLUMN_BOXES),
                ]
            )
        else:
            raise NotImplementedError(f"Task type: {self._task} is not supported yet.")
        return ModelSignature(inputs=input_schema, outputs=output_schema)

    def save_as_mlflow(self):
        """Prepare model for save to MLflow."""
        sys.path.append(self.MODEL_DIR)
        from track_predict import VideosTrackingMLflowModelWrapper

        mlflow_model_wrapper = VideosTrackingMLflowModelWrapper(task_type=self._task)
        artifacts_dict = self._prepare_artifacts_dict()
        conda_env = os.path.join(self.MODEL_DIR, "conda.yaml")
        code_path = [
            os.path.join(self.MODEL_DIR, "track_predict.py"),
            os.path.join(self.MODEL_DIR, "config.py"),
            os.path.join(self.COMMON_DIR, "vision_utils.py")
        ]
        super()._save(
            mlflow_model_wrapper=mlflow_model_wrapper,
            artifacts_dict=artifacts_dict,
            conda_env=conda_env,
            code_path=code_path,
        )

    def _prepare_artifacts_dict(self) -> Dict:
        """Prepare artifacts dict for MLflow model.

        :return: artifacts dict
        :rtype: Dict
        """
        metadata_path = os.path.join(self._model_dir, "model_selector_args.json")
        with open(metadata_path) as f:
            metadata = json.load(f)

        artifacts_dict = {
            MMDetLiterals.CONFIG_PATH: os.path.join(self._model_dir, metadata.get("pytorch_model_path")),
            MMDetLiterals.WEIGHTS_PATH: os.path.join(self._model_dir, metadata.get("model_weights_path_or_url")),
            MMDetLiterals.METAFILE_PATH: os.path.join(self._model_dir, metadata.get("model_metafile_path")),
        }
        return artifacts_dict
