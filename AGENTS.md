# Quickflash public source

This checkout is a generated Publicate snapshot. Make source changes in the
private project and publish them through Publicate.

Report issues through the private Git ticket endpoint in `PUBLICATE.json`, using
an installed Publicate command and authorized Git credentials:

```sh
publicate ticket --title 'Issue title' --body-file /path/to/report.txt \
  --code-version vMAJOR.MINOR.PATCH --push
```

Include the reproducer, expected behavior and observed result. Ticket access
uses the private repository's Git permissions. Keep credentials outside reports.
Pin consumers to the snapshot's resolved commit and Nix content hash.
