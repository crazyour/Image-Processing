# Bundled inference runtime

This directory contains the Linux x86_64 CPU inference core from
`onnxruntime==1.24.4`. Only the runtime package root and `capi` files are
included; training, quantization, transformer tooling, sample models, and
the optional SymPy dependency are intentionally omitted.

ONNX Runtime is copyright Microsoft Corporation and is distributed under
the MIT License. The upstream `LICENSE` and `ThirdPartyNotices.txt` files
are kept inside `onnxruntime/`.
