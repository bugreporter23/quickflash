"""Measure compressed cache duplication without guessing from installed sizes."""

from pathlib import Path


def cache_overlap(cache, store_paths):
    sizes, shared, exclusive = {}, set(), set()
    shared_paths = 0
    for path in Path(cache).glob("*.narinfo"):
        info = dict(line.split(": ", 1) for line in path.read_text().splitlines())
        url = info["URL"]
        sizes[url] = int(info["FileSize"])
        if info["StorePath"] in store_paths:
            shared.add(url)
            shared_paths += 1
        else:
            exclusive.add(url)
    removable = shared - exclusive
    return {
        "cache_nar_bytes": sum(sizes.values()),
        "shared_cache_bytes": sum(sizes[url] for url in removable),
        "shared_cache_paths": shared_paths,
    }
