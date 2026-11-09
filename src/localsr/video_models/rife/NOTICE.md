# RIFE vendored code — attribution

`ifnet.py` contains the inference part of IFNet from Practical-RIFE by hzwer
(https://github.com/hzwer/Practical-RIFE), taken from `train_log/IFNet_HDv3.py` of the
official v4.26 model archive (the v4.25 archive contains the same file) and from
`model/warplayer.py` at commit `bbfd2ea90910789a860ea3e2b32a240cd577b75e`. RIFE is
described in Huang et al., "Real-Time Intermediate Flow Estimation for Video Frame
Interpolation", ECCV 2022.

- License: MIT, reproduced below. The Practical-RIFE README states that the trained
  models it links are "under the same MIT license as this project".
- Modifications by LocalSR are listed at the top of `ifnet.py`: inference only, a
  per-module warp grid sampled in FP32 (as HolyWu/vs-rife, MIT, does), and encoder
  features that several timesteps can share.
- The weights are not bundled. The catalog downloads `flownet_v4.25.pkl` from
  HolyWu/vs-rife's `model` release, whose bytes are identical to `train_log/flownet.pkl`
  in the official v4.25 Google Drive archive; see `docs/model-licenses.md`.

```
MIT License

Copyright (c) 2021 hzwer

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
