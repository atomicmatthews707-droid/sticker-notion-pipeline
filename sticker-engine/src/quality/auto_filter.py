def filter_batch(images: list[dict], niche: str) -> None:
    """
    Vision QA (Batch 3). Contract: for each pending image record, score it against
    config/prompts/qa_rubric.md and call db.update_image(id, qa_score=..., qa_reason=..., kept=...).
    """
    raise NotImplementedError("auto_filter.filter_batch is not built yet (Batch 3: vision QA gate)")
