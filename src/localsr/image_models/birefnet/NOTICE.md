# BiRefNet inference code and weights

BiRefNet is by ZhengPeng and contributors. This directory adapts the inference
network in `birefnet.py` from the Hugging Face repository
[ZhengPeng7/BiRefNet](https://huggingface.co/ZhengPeng7/BiRefNet), revision
`e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4`. Its components derive from the
upstream files `models/backbones/swin_v1.py`, `models/modules/deform_conv.py`,
`models/modules/decoder_blocks.py`, `models/modules/lateral_blocks.py`,
`models/modules/aspp.py`, and `models/birefnet.py`. The
[upstream repository and license](https://github.com/ZhengPeng7/BiRefNet/blob/f7079632d681ecf12161fcea8905682934e59e51/LICENSE)
are MIT; the model repository identifies the weights as MIT as well. LocalSR
uses only the general checkpoint, downloaded separately and verified by size
and SHA-256. LocalSR changed the code to run inference locally without
transformers, timm, kornia, or network access.

The timm helpers previously imported by `models/backbones/swin_v1.py` are
replaced locally: PyTorch's `nn.init.trunc_normal_`, a tuple conversion, and
an inference identity for stochastic depth. No timm code is copied. Kornia's
Laplacian was used only for training and is omitted. Deformable convolution
still uses `torchvision.ops.deform_conv2d`.

```text
MIT License

Copyright (c) 2024 ZhengPeng

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The Swin Transformer backbone was written by Ze Liu, Yutong Lin, and Yixuan
Wei and is also distributed under the MIT license:

```text
MIT License

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
