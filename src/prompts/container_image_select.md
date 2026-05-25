# Container Image Selection

You are selecting a container image for the SEAM migration framework.

## Goal

Choose **exactly one** container image from the provided candidate list.
The framework will create the container — you do NOT create containers.

## Candidate Images

{candidate_images}

{discovered_images_section}

{selection_guidance}

## Hard Rules

- **CRITICAL: Container Lifecycle Safety** — You MUST NOT run ANY of these commands:
  `docker run`, `docker create`, `docker start`, `docker exec`, `docker stop`, `docker rm`,
  `podman run`, `podman create`, `podman start`, `podman exec`, `podman stop`, `podman rm`.
  Container creation and management is handled exclusively by the SEAM framework.
  Your only job is to **select** which image the framework should use.

- Select **exactly one** image from the candidate list above.

- Your selection MUST be one of the listed images verbatim. Do NOT modify, combine,
  or invent image names.

- If no images are listed in the candidate section and no discovered images are
  provided, respond with `{"selected_image": "__none__"}` to signal that no
  suitable image is available.

- Return ONLY a single JSON object with the key `selected_image`. Do NOT include
  any other text, JSON, or markdown after this object.

## Required Output

```json
{"selected_image": "<exact image name from the candidate list>"}
```
