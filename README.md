# Kelee Loon Plugins for Surge

This repository contains Surge modules converted from the Loon plugins listed at:

```text
https://hub.kelee.one/
```

## Modules

Converted modules are in:

```text
modules/
```

Full module index:

[Open modules index](modules/README.md)

## One-Tap Import

Open these links on the device where Surge is installed:

| Module | Raw URL | One-tap import |
| --- | --- | --- |
| YouTube 去广告 | [Raw](https://raw.githubusercontent.com/zwjtano/kelee-loon-surge-modules/master/modules/YouTube_remove_ads.sgmodule) | [Import](https://link.lxya.de/surge/install-module?url=https%3A%2F%2Fraw.githubusercontent.com%2Fzwjtano%2Fkelee-loon-surge-modules%2Fmaster%2Fmodules%2FYouTube_remove_ads.sgmodule) |
| Bilibili 去广告 | [Raw](https://raw.githubusercontent.com/zwjtano/kelee-loon-surge-modules/master/modules/Bilibili_remove_ads.sgmodule) | [Import](https://link.lxya.de/surge/install-module?url=https%3A%2F%2Fraw.githubusercontent.com%2Fzwjtano%2Fkelee-loon-surge-modules%2Fmaster%2Fmodules%2FBilibili_remove_ads.sgmodule) |
| Zhihu 去广告 | [Raw](https://raw.githubusercontent.com/zwjtano/kelee-loon-surge-modules/master/modules/Zhihu_remove_ads.sgmodule) | [Import](https://link.lxya.de/surge/install-module?url=https%3A%2F%2Fraw.githubusercontent.com%2Fzwjtano%2Fkelee-loon-surge-modules%2Fmaster%2Fmodules%2FZhihu_remove_ads.sgmodule) |
| 小红书去广告 | [Raw](https://raw.githubusercontent.com/zwjtano/kelee-loon-surge-modules/master/modules/RedPaper_remove_ads.sgmodule) | [Import](https://link.lxya.de/surge/install-module?url=https%3A%2F%2Fraw.githubusercontent.com%2Fzwjtano%2Fkelee-loon-surge-modules%2Fmaster%2Fmodules%2FRedPaper_remove_ads.sgmodule) |
| Spotify 去广告 | [Raw](https://raw.githubusercontent.com/zwjtano/kelee-loon-surge-modules/master/modules/Spotify_remove_ads.sgmodule) | [Import](https://link.lxya.de/surge/install-module?url=https%3A%2F%2Fraw.githubusercontent.com%2Fzwjtano%2Fkelee-loon-surge-modules%2Fmaster%2Fmodules%2FSpotify_remove_ads.sgmodule) |

Every module in [modules/README.md](modules/README.md) also has a one-tap Surge import link.

## Status

The latest local conversion result:

```text
Converted: 232, needs review: 0, failed: 0
```

Full report:

```text
conversion-report.json
```

## Sync

The workflow at `.github/workflows/update-kelee-loon-to-surge.yml` periodically downloads the Loon plugins with a Loon-style User-Agent and regenerates `modules/`.

Conversion script:

```text
scripts/convert_kelee_loon_to_surge.py
```

Source plugins belong to their original authors. This repository only keeps an automatically converted Surge-format mirror.
