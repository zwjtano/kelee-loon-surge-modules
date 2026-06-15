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

Example raw URL:

```text
https://raw.githubusercontent.com/zwjtano/kelee-loon-surge-modules/master/modules/YouTube_remove_ads.sgmodule
```

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
