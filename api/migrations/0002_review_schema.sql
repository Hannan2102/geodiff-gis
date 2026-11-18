-- GeoDiff review schema. Datasets and snapshots are immutable; review state lives in
-- `decisions` (versioned per component) and `review_events` (append-only audit).

CREATE TABLE workspaces (
    id            uuid PRIMARY KEY,
    token_hash    bytea NOT NULL UNIQUE,          -- sha256 of the bearer token; token never stored
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL
);
CREATE INDEX workspaces_expires_idx ON workspaces (expires_at);

CREATE TABLE datasets (
    id             uuid PRIMARY KEY,
    workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name           text NOT NULL,
    sha256         char(64) NOT NULL,
    size_bytes     integer NOT NULL,
    feature_count  integer NOT NULL,
    valid          boolean NOT NULL,
    summary        jsonb NOT NULL,                 -- fields, candidate IDs, sampled issues
    original       bytea NOT NULL,                 -- retained upload, private to the workspace
    source         jsonb,                          -- attribution for imported public layers
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, sha256)
);

CREATE TABLE snapshots (
    id             uuid PRIMARY KEY,
    dataset_id     uuid NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    id_field       text NOT NULL,
    feature_count  integer NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, id_field)
);

CREATE TABLE features (
    snapshot_id  uuid NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    asset_id     text NOT NULL,                    -- normalized ID (see docs/ENGINE.md)
    ordinal      integer NOT NULL,                 -- position in the source file (informational)
    geom         geometry(Point, 4326) NOT NULL,
    feature      jsonb NOT NULL,                   -- normalized GeoJSON feature, exact values
    PRIMARY KEY (snapshot_id, asset_id)
);
CREATE INDEX features_geom_idx ON features USING gist (geom);

CREATE TABLE comparisons (
    id                    uuid PRIMARY KEY,
    workspace_id          uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    baseline_snapshot_id  uuid NOT NULL REFERENCES snapshots(id),
    proposed_snapshot_id  uuid NOT NULL REFERENCES snapshots(id),
    parent_id             uuid REFERENCES comparisons(id) ON DELETE SET NULL,
    tolerance_m           double precision NOT NULL CHECK (tolerance_m >= 0),
    compared_fields       text[] NOT NULL,
    parameters            jsonb NOT NULL,
    inputs                jsonb NOT NULL,
    summary               jsonb NOT NULL,
    warnings              jsonb NOT NULL,
    engine_version        text NOT NULL,
    revision              bigint NOT NULL DEFAULT 0,  -- bumps on every review change
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX comparisons_workspace_idx ON comparisons (workspace_id, created_at DESC);

CREATE TABLE change_components (
    comparison_id  uuid NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    component_id   text NOT NULL,
    asset_id       text NOT NULL,
    asset_status   text NOT NULL CHECK (asset_status IN ('added', 'deleted', 'modified')),
    kind           text NOT NULL CHECK (kind IN ('addition', 'deletion', 'move', 'attribute')),
    field          text,
    ordinal        integer NOT NULL,               -- order within the asset
    evidence       jsonb NOT NULL,
    patch          jsonb NOT NULL,
    distance_m     double precision,
    geom_from      geometry(Point, 4326),
    geom_to        geometry(Point, 4326),
    PRIMARY KEY (comparison_id, component_id)
);
CREATE INDEX change_components_asset_idx ON change_components (comparison_id, asset_id, ordinal);
CREATE INDEX change_components_kind_idx ON change_components (comparison_id, kind);
CREATE INDEX change_components_from_idx ON change_components USING gist (geom_from);
CREATE INDEX change_components_to_idx ON change_components USING gist (geom_to);

CREATE TABLE comparison_assets (
    comparison_id  uuid NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    asset_id       text NOT NULL,
    status         text NOT NULL,
    baseline       jsonb,                          -- {coordinates, properties}
    proposed       jsonb,
    PRIMARY KEY (comparison_id, asset_id)
);

CREATE TABLE decisions (
    comparison_id  uuid NOT NULL,
    component_id   text NOT NULL,
    disposition    text NOT NULL DEFAULT 'pending'
                   CHECK (disposition IN ('pending', 'accepted', 'rejected', 'flagged')),
    note           text,
    decided_at     timestamptz,
    version        integer NOT NULL DEFAULT 0,
    PRIMARY KEY (comparison_id, component_id),
    FOREIGN KEY (comparison_id, component_id)
        REFERENCES change_components (comparison_id, component_id) ON DELETE CASCADE,
    CHECK (disposition <> 'flagged' OR length(btrim(coalesce(note, ''))) > 0)
);
CREATE INDEX decisions_disposition_idx ON decisions (comparison_id, disposition);

CREATE TABLE review_events (
    id               bigserial PRIMARY KEY,
    comparison_id    uuid NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    batch_id         uuid NOT NULL,                -- one user action (single, bulk or undo)
    action           text NOT NULL CHECK (action IN ('decide', 'bulk', 'undo')),
    component_id     text NOT NULL,
    from_disposition text NOT NULL,
    to_disposition   text NOT NULL,
    from_note        text,
    note             text,
    to_version       integer NOT NULL,
    revision         bigint NOT NULL,              -- comparison revision after this action
    undone_by        uuid,                         -- batch_id of the undo that reverted it
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (comparison_id, component_id, to_version)
);
CREATE INDEX review_events_batch_idx ON review_events (comparison_id, revision DESC);
