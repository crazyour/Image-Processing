# Semantic search model

`mobilenetv2-12-qdq.onnx` is the quantized MobileNetV2 model published by the
official ONNX Model Zoo mirror:

- Source: https://huggingface.co/onnxmodelzoo/mobilenetv2-12-qdq
- Model license: Apache-2.0
- SHA-256: `41a36090dafe98f4ad8f9b7fe0b218c56ac3c031e547f0367c30655d2702bffe`

The application uses its ImageNet score vector as a lightweight CPU semantic
embedding. It does not use the model to make or display a hard class label.
