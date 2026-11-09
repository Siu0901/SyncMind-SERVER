import enum


class IngestionJobStatus(enum.StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestionStage(enum.StrEnum):
    DOWNLOADING = "downloading"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
