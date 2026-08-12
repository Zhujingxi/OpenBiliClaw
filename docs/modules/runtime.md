# Runtime

Runtime ownership is split between `core/` (jobs, lifecycle, resources, health) and `composition/` (concrete graph, reload, process entrypoints). The deleted legacy `runtime/` package has no compatibility surface. See [composition](composition.md).
