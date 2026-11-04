# Notice: pyannote speaker-diarization-community-1

This directory is an unmodified copy of a third-party model. It is not covered by the MIT license of this repository.

- **Title:** speaker-diarization-community-1
- **Creator:** pyannote (Hervé Bredin, Alexis Plaquet and contributors), <https://www.pyannote.ai>
- **Source:** <https://huggingface.co/pyannote/speaker-diarization-community-1>,
  revision `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee`
- **License:** Creative Commons Attribution 4.0 International (CC BY 4.0),
  <https://creativecommons.org/licenses/by/4.0/>
- **Changes:** none. Files are byte-identical to the source revision; `README.md` is the original model card.

Bundled components, as distributed in the source revision:

| Path | Component | Credit | License |
| --- | --- | --- | --- |
| `segmentation/` | powerset speaker segmentation | Plaquet & Bredin, INTERSPEECH 2023 | CC BY 4.0 |
| `embedding/` | WeSpeaker ResNet34-LM, trained on VoxCeleb | Wang et al., ICASSP 2023; [wespeaker-voxceleb-resnet34-LM](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM) | CC BY 4.0 |
| `plda/` | VBx PLDA | BUT Speech@FIT; Landini et al., CSL 2022 | CC BY 4.0 (as part of the pipeline) |

Citations (BibTeX) are in [README.md](README.md#citations).

The original publisher distributes this model behind a Hugging Face form that asks for contact details. It is
copied here so that the tool works without an account. If you use the model for research or want pyannote
news, please register on the model page and consider their paid pipelines.

## Disclaimer

The model is provided by its licensors "as is", without warranties of any kind, as stated in Section 5 of
CC BY 4.0. Neither the licensors nor the maintainers of this repository endorse this repository or its use.
