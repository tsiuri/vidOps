#!/usr/bin/env python3
"""
Migrate drills from config_json JSONB to dedicated drills table.

Strategy:
1. Create new drills table (schema.sql handles this)
2. Extract all drills from all configs
3. Deduplicate identical drills across configs -> global
4. Keep config-specific drills as local
5. Preserve dependencies
6. Validate migration
7. Backup config_json before removal

Usage:
    python scripts/migrate_drills_to_db.py [--dry-run] [--keep-backup]
"""

import psycopg2
import json
import hashlib
import argparse
from typing import Dict, List, Any, Set, Tuple
from datetime import datetime
import sys

def hash_drill_content(drill: Dict[str, Any]) -> str:
    """Create hash of drill properties (excluding name/description).

    Used to identify identical drills across configs.
    """
    normalized = {
        k: v for k, v in drill.items()
        if k not in ('name', 'description')
    }
    content = json.dumps(normalized, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


def get_db_config() -> Dict[str, Any]:
    """Load database configuration from environment or defaults."""
    import os
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'database': os.getenv('DB_NAME', 'transcripts'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', ''),
    }


def migrate_drills(dry_run: bool = False, keep_backup: bool = True) -> bool:
    """Main migration function.

    Args:
        dry_run: If True, don't commit changes
        keep_backup: If True, keep 'drills' in config_json

    Returns:
        True if successful, False otherwise
    """

    db_config = get_db_config()

    try:
        conn = psycopg2.connect(**db_config)
    except psycopg2.Error as e:
        print(f"❌ Failed to connect to database: {e}")
        return False

    cursor = conn.cursor()

    try:
        print("\n📋 Starting drill migration...\n")

        # Step 1: Verify tables exist
        print("1️⃣  Verifying drills table exists...")
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'drills'
            )
        """)
        if not cursor.fetchone()[0]:
            print("❌ Drills table does not exist. Run: psql -f schema.sql")
            conn.close()
            return False
        print("   ✅ Drills table exists\n")

        # Step 2: Extract all drills from all configs
        print("2️⃣  Extracting drills from analysis configs...")
        cursor.execute("""
            SELECT id, name, config_json
            FROM analysis_configs
            WHERE config_json ? 'drills'
        """)

        config_drills: Dict[str, List[Dict]] = {}
        drill_signatures: Dict[str, Set[str]] = {}  # hash -> set of config_ids

        results = cursor.fetchall()
        for config_id, config_name, config_json in results:
            drills = config_json.get('drills', [])
            if not drills:
                continue

            config_drills[config_id] = drills

            for drill in drills:
                sig = hash_drill_content(drill)
                drill_signatures.setdefault(sig, set()).add(config_id)

        total_drills = sum(len(d) for d in config_drills.values())
        print(f"   Found drills in {len(config_drills)} configs")
        print(f"   Total drill instances: {total_drills}\n")

        # Step 3: Determine global vs local
        # Drills appearing in 2+ configs -> global (deduplicate)
        # Drills in 1 config -> local
        global_drill_sigs = {
            sig for sig, configs in drill_signatures.items()
            if len(configs) >= 2
        }

        print("3️⃣  Categorizing drills...")
        print(f"   Global drill patterns: {len(global_drill_sigs)}")
        print(f"   Local drill patterns: {len(drill_signatures) - len(global_drill_sigs)}\n")

        # Step 4: Insert drills
        print("4️⃣  Inserting drills into database...")

        # Track drill names by (name, config_id) -> database id
        drill_id_map: Dict[Tuple[str, str | None], int] = {}
        global_drill_count = 0
        local_drill_count = 0

        if dry_run:
            print("   🔄 DRY RUN MODE - no data will be saved\n")

        # Insert global drills first
        print("   Global drills:")
        for sig in global_drill_sigs:
            # Find first occurrence to use as template
            for config_id, drills in config_drills.items():
                for drill in drills:
                    if hash_drill_content(drill) == sig:
                        # Insert as global (config_id = NULL)
                        drill_id = insert_drill(cursor, drill, config_id=None, dry_run=dry_run)
                        drill_id_map[(drill['name'], None)] = drill_id
                        print(f"     • {drill['name']} (id: {drill_id})")
                        global_drill_count += 1
                        break
                else:
                    continue
                break

        print("\n   Local drills:")
        local_drills_by_config: Dict[str, int] = {}
        for config_id, drills in config_drills.items():
            local_count = 0
            for drill in drills:
                sig = hash_drill_content(drill)
                if sig in global_drill_sigs:
                    continue  # Already inserted as global

                # Insert as local (config_id = <id>)
                drill_id = insert_drill(cursor, drill, config_id=config_id, dry_run=dry_run)
                drill_id_map[(drill['name'], config_id)] = drill_id
                print(f"     • {config_id[:12]}... / {drill['name']} (id: {drill_id})")
                local_drill_count += 1
                local_count += 1

            if local_count > 0:
                local_drills_by_config[config_id] = local_count

        print(f"\n   Total inserted: {global_drill_count} global + {local_drill_count} local\n")

        # Step 5: Insert dependencies
        print("5️⃣  Migrating drill dependencies...")
        dep_count = 0

        for config_id, drills in config_drills.items():
            for drill in drills:
                depends_on = drill.get('depends_on', [])
                if not depends_on:
                    continue

                # Find drill ID (local or global)
                sig = hash_drill_content(drill)
                if sig in global_drill_sigs:
                    drill_id = drill_id_map.get((drill['name'], None))
                else:
                    drill_id = drill_id_map.get((drill['name'], config_id))

                if not drill_id:
                    print(f"   ⚠️  WARNING: Could not find drill '{drill['name']}' from config {config_id}")
                    continue

                for dep_name in depends_on:
                    # Try to find dependency (check local first, then global)
                    dep_id = drill_id_map.get((dep_name, config_id))
                    if not dep_id:
                        dep_id = drill_id_map.get((dep_name, None))

                    if dep_id:
                        insert_dependency(cursor, drill_id, dep_id, dry_run=dry_run)
                        dep_count += 1
                    else:
                        print(f"   ⚠️  WARNING: Drill '{drill['name']}' depends on '{dep_name}' which was not found")

        print(f"   Inserted {dep_count} dependency relationships\n")

        # Step 6: Validation
        print("6️⃣  Validating migration...")
        if not dry_run:
            cursor.execute("SELECT COUNT(*) FROM drills")
            total_in_db = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM drills WHERE config_id IS NULL")
            global_in_db = cursor.fetchone()[0]

            local_in_db = total_in_db - global_in_db

            print(f"   Database now contains:")
            print(f"     • {total_in_db} total drills")
            print(f"     • {global_in_db} global drills")
            print(f"     • {local_in_db} local drills")

            # Verify counts match
            expected_total = global_drill_count + local_drill_count
            if total_in_db != expected_total:
                print(f"   ⚠️  WARNING: Expected {expected_total} drills but found {total_in_db}")
            else:
                print(f"   ✅ Drill counts match expectations")
        else:
            print(f"   (Skipped in dry-run mode)\n")

        # Step 7: Backup and optionally clean config_json
        print("7️⃣  Handling config_json backup...")

        if not dry_run:
            # Create backup table if it doesn't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analysis_configs_backup_pre_drills_migration AS
                SELECT * FROM analysis_configs
                WHERE config_json ? 'drills'
            """)

            if not keep_backup:
                # Remove 'drills' from config_json
                cursor.execute("""
                    UPDATE analysis_configs
                    SET config_json = config_json - 'drills',
                        updated_at = NOW()
                    WHERE config_json ? 'drills'
                """)
                cursor.execute("SELECT changes() FROM (SELECT 1) t")
                rows_updated = cursor.rowcount
                print(f"   Removed 'drills' from {rows_updated} config entries\n")
            else:
                print(f"   Keeping 'drills' in config_json for safety\n")
        else:
            print(f"   (Skipped in dry-run mode)\n")

        # Commit or rollback
        if dry_run:
            print("🔄 DRY RUN - Rolling back all changes...")
            conn.rollback()
            print("✅ Dry-run complete. No changes were saved.\n")
        else:
            print("💾 Committing all changes...")
            conn.commit()
            print("✅ Migration complete!\n")

        print("Summary:")
        print(f"  • {global_drill_count} global drills created")
        print(f"  • {local_drill_count} local drills created")
        print(f"  • {dep_count} dependencies created")
        print(f"  • {len(config_drills)} configs processed\n")

        return True

    except psycopg2.Error as e:
        print(f"\n❌ Database error: {e}")
        if not dry_run:
            conn.rollback()
        conn.close()
        return False
    except Exception as e:
        print(f"\n❌ Error: {e}")
        if not dry_run:
            conn.rollback()
        conn.close()
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def insert_drill(cursor, drill: Dict[str, Any], config_id: str | None, dry_run: bool = False) -> int:
    """Insert a drill and return its ID."""

    if dry_run:
        # Return a fake ID for dry-run
        return abs(hash(drill['name'])) % 100000

    cursor.execute("""
        INSERT INTO drills (
            name, description, prompt, scope, output_shape, category,
            always, min_hits, keywords, match, cooldown, detail_pass, config_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
        ) RETURNING id
    """, (
        drill['name'],
        drill.get('description', ''),
        drill.get('prompt', ''),
        drill.get('scope', 'chunks'),
        drill.get('output_shape', 'span'),
        drill.get('category'),
        drill.get('always', False),
        drill.get('min_hits', 0),
        drill.get('keywords', []),
        drill.get('match', []),
        drill.get('cooldown', 0),
        json.dumps(drill.get('detail_pass', {})),
        config_id
    ))

    return cursor.fetchone()[0]


def insert_dependency(cursor, drill_id: int, depends_on_id: int, dry_run: bool = False) -> None:
    """Insert a dependency relationship."""

    if dry_run:
        return

    cursor.execute("""
        INSERT INTO drill_dependencies (drill_id, depends_on_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
    """, (drill_id, depends_on_id))


def main():
    parser = argparse.ArgumentParser(
        description='Migrate drills from config_json JSONB to dedicated database table'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate the migration without making changes'
    )
    parser.add_argument(
        '--keep-backup',
        action='store_true',
        default=True,
        help='Keep "drills" in config_json as backup (default: True)'
    )
    parser.add_argument(
        '--no-keep-backup',
        action='store_false',
        dest='keep_backup',
        help='Remove "drills" from config_json after migration'
    )

    args = parser.parse_args()

    success = migrate_drills(dry_run=args.dry_run, keep_backup=args.keep_backup)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
