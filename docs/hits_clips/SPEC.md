# VidOps Hits & Clips Project Specification

## 1. Overview

This document specifies the **Hits & Clips Project System** for VidOps.  
It defines the ontology, lifecycle, DAG semantics, retention rules, and WebUI expectations
for generating, managing, and projecting *hits* into *clips* in a storage-conscious,
analysis-driven media pipeline.

This spec is authoritative for implementation.

---

## 2. Core Concepts

### 2.1 Project

A **Project** is the top-level container for:
- input video sets
- analysis configurations
- hit generators and rules
- clip profiles
- DAG runs and artifacts
- retention and finalization policies

Finalization and cleanup operate **at the project level**, not per video or per clip.

---

### 2.2 Hit

A **Hit** is a persisted, first-class metadata object representing
a claim that *something interesting occurs at specific time spans* in media.

#### Required Properties
- Temporal: one or more time spans (always required)
- Logical identity (stable across regenerations)
- Explicit version number
- Type (`keyword | analysis | hybrid | manual | meta`)
- Confidence score (normalized 0–1)
- Provenance (generator, config versions, analysis pass IDs)

#### Notes
- Hits never store video data by default.
- Hits may overlap in time.
- Hits may aggregate non-contiguous spans if explicitly configured.

---

### 2.3 Hit Identity & Versioning

- **hit_logical_id**: stable identifier for a conceptual hit
- **hit_version**: explicit integer, incremented on regeneration
- Regeneration creates a new version; old versions persist unless pruned
- Garbage collection may remove old versions per policy unless pinned

---

### 2.4 Hit Status

Hits support explicit statuses:
- `active`
- `stale`
- `rejected`
- `archived`

Rules:
- Rejected hits are retained as metadata
- Rejected hits may be regenerated later
- Hits may be **pinned**, preventing invalidation or deletion

---

### 2.5 Suggestions

Analysis systems may emit **Suggestions**:
- Temporal or chunk-referenced candidates
- Not hits
- Stored separately
- Can be promoted into hits by rules or manual action

---

## 3. Hit Sources

### 3.1 Keyword Hits
- Word-level timing (primary)
- Segment-level timing (optional)
- Speaker-aware (diarization)
- Regex, phrase windows, exclusions
- Escalation by repetition or density
- Gated by analysis outputs if configured

### 3.2 Analysis Hits
- Derived from drills and analysis passes
- May involve voting across passes
- Confidence normalized across drill types
- Non-determinism explicitly allowed

### 3.3 Hybrid Hits
- Keyword logic gated or shaped by analysis
- Analysis results gated by transcript signals

### 3.4 Manual Hits
- User-authored via WebUI
- Participate fully in DAG logic where appropriate

### 3.5 Meta-Hits
- Hits derived from other hits
- Support aggregation, escalation, and reasoning chains

---

## 4. DAG Runtime

### 4.1 Node Types

Nodes may emit:
- suggestions
- hits
- clips
- zero results

Zero results are acceptable by default.

### 4.2 Failure Semantics

Each node may declare:
- `zero_is_ok`
- `zero_is_warning`
- `zero_is_failure`

### 4.3 Invalidation Policy

Each node declares an invalidation mode:
- **strict**: upstream change invalidates downstream outputs
- **soft**: downstream marked stale but usable
- **manual**: no automatic invalidation

### 4.4 Partial Reruns

Partial DAG reruns are allowed if:
- Required upstream artifacts exist
- Artifacts have not expired

Otherwise, nodes enter a **blocked** state with diagnostics.

---

## 5. Clip System

### 5.1 Clip Definition

A **Clip** is a derived artifact produced from:
- a hit snapshot
- a clip profile (name + version)

Clips store:
- hit snapshot used
- profile identity and version
- limited provenance metadata

Clips may outlive hits.

### 5.2 Clip Profiles

- Rule-based
- Versioned
- Reusable across projects
- Avoid data duplication

### 5.3 Video Storage

- Video storage is **opt-in**
- Default behavior stores metadata and paths only
- Rendering may be deferred or on-demand

---

## 6. Retention, Expiration & Finalization

### 6.1 Policy Precedence

Retention policies exist at:
1. Node level
2. Project level
3. Global defaults

More granular settings override broader ones.

### 6.2 Artifact Classes

- **Persistent**: hits (metadata), pinned items, configs, clip metadata
- **Ephemeral**: intermediate analysis outputs, caches, staging data

### 6.3 Project Finalization

Finalization:
- Freezes selected hit versions and clips
- Aggressively prunes ephemeral artifacts
- Preserves reproducibility where policy allows

---

## 7. Metrics & Feedback

### 7.1 Generator Metrics
- Hits produced
- Acceptance / rejection rates
- Conversion to clips
- Retention after finalization

### 7.2 Optional Feedback Signals
- Clip rendered
- Clip exported
- Clip viewed / favorited
- Used as optional weighting signals

---

## 8. WebUI Expectations

### 8.1 Project Dashboard
- Storage footprint
- Pinned items
- Stale artifacts
- Finalization controls

### 8.2 DAG Editor
- Visual Analysis → Hit → Clip chaining
- Per-node configuration
- Partial rerun controls
- Blocked-state diagnostics

### 8.3 Hit Explorer
- Timeline and list views
- Filters by type, status, generator, speaker, confidence
- Version comparison
- Pin / reject / restore

### 8.4 Clip Studio
- Group by hit logical ID
- Multiple clip profiles
- Regeneration controls
- Opt-in rendering

### 8.5 Templates & Presets
- First-class reusable objects
- Shareable across projects

---

## 9. Non-Goals

- Mandatory determinism
- Implicit clip creation without hits
- Automatic video storage

---

## 10. Summary

This system establishes **Hits as the semantic core**
and **Clips as projections**, enabling scalable,
storage-aware, analysis-driven media intelligence
with explicit provenance, versioning, and user control.
