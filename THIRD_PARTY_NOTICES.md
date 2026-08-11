# Third-Party Notices

## Presentation AI

Parts of `app/services/pptx_parser.py` are Python adaptations of the OOXML relationship,
color-map, theme-palette, and font-scheme extraction algorithms in
`allweonedev/presentation-ai`, commit `43fe74abd5676dbbea52b8cd66c43fb3b931b5c1`.

Copyright (c) 2024 ALLWEONE Team

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of this software
and associated documentation files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.

## pptx-renderer

Parts of `app/services/pptx_parser.py` are Python adaptations of Theme, Master, Layout,
relationship, placeholder geometry, and inheritance algorithms in `aiden0z/pptx-renderer`.
The adaptation is based on commit `68cb570940fb28d5c4628f31d1365016c4483521`.

Copyright (c) pptx-renderer contributors

Licensed under the Apache License, Version 2.0. You may obtain a copy of the License at
<https://www.apache.org/licenses/LICENSE-2.0>. Unless required by applicable law or agreed to
in writing, software distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

## FontTools

The presentation text-layout subsystem uses FontTools to read advance widths and metadata
from trusted local OpenType font files. No FontTools source code is copied into this project.

Copyright (c) 2017 The FontTools Authors

Licensed under the MIT License. See <https://github.com/fonttools/fonttools>.

## Noto Sans SC

The R1 Chinese presentation capacity baseline bundles Noto Sans SC Variable Font from
`google/fonts`, revision `038b637da7b3fd956a4ed93ffc607c3d5e4ce172`.

Copyright 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name
"Source". Noto Sans SC is distributed under the SIL Open Font License, Version 1.1.
The license text is retained at `app/assets/presentation/licenses/FONT-OFL-1.1.txt`.
