"""Discover each tournament independently; a partial outage is never an empty queue."""
import logging

logger = logging.getLogger(__name__)


def discover_open_questions(client, projects):
    questions, errors, successful = [], [], []
    for project in dict.fromkeys(projects):
        try:
            found = client.get_all_open_questions_from_tournament(project)
            questions.extend(found)
            successful.append(project)
        except Exception as exc:
            error = {"project": project, "error_type": type(exc).__name__}
            errors.append(error)
            logger.warning("::warning::Question discovery unavailable for %s (%s); other projects continue", project, type(exc).__name__)
    if not successful:
        raise RuntimeError("All tournament discovery requests failed; no model calls made")
    return questions, errors
