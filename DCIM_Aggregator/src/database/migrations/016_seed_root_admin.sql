-- =============================================================================
-- 016_seed_root_admin.sql — Seed the bootstrap ROOT super-user.
--
-- Creates (or refreshes) a root account:
--   username : admin
--   email    : faberadmin@gmail.com
--   password : 123456   (scrypt hash below — format "<salt-hex>:<key-hex>",
--                         generated with the same crypto.scrypt the API uses)
--   status   : approved
--   role     : root      (stored in users.requested_role AND user_roles)
--
-- Because requested_role = 'root', the API recognises this account as an
-- all-access super-user (full "manage" on every feature) and an approver, so it
-- can approve sign-ups and grant any role, including root and admin.
--
-- Idempotent: safe to re-run. Re-running resets this user's email / password /
-- status / role back to the values below.
-- =============================================================================

WITH upserted AS (
  INSERT INTO users (username, email, password_hash, status, requested_role)
  VALUES (
    'admin',
    'faberadmin@gmail.com',
    '43b350d7ed245b4b71fb05ac7c8e7fee:869e981c435e20cb30266af0f6f8403c22f4b7c7208ff4b945c45e8829bf17e18e26a1dbf5f8132074d6455d2ad30ead1ab0960ef19dd937b947255d00023bdf',
    'approved',
    'root'
  )
  ON CONFLICT (username) DO UPDATE
    SET email         = EXCLUDED.email,
        password_hash = EXCLUDED.password_hash,
        status        = 'approved',
        requested_role = 'root'
  RETURNING id
)
INSERT INTO user_roles (user_id, role, granted_by)
SELECT id, 'root', id FROM upserted
ON CONFLICT (user_id, role) DO NOTHING;
