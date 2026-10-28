# The credential master key

The controller encrypts device credentials with a data key per customer, and stores each data key
wrapped by one master key (ADR-0003). Engineers' authenticator keys are wrapped by the same master
key. The master key never goes into the database: the controller reads it from the file named by
`CARTIO_MASTER_KEY_FILE` (or from `CARTIO_MASTER_KEY`).

## Back it up

Without the master key, nothing it wrapped can be opened again: every credential has to be
re-entered and every engineer has to enroll their authenticator again.

- Generate it with `openssl rand -base64 32`, into a file only the controller's user can read
  (`chmod 600`).
- Keep a copy somewhere other than the controller and its database backups, for example a password
  manager or a sealed envelope. A database backup stored next to its master key protects nothing.
- Each database backup needs the master key that was current when it was made. Keep an old key
  for as long as backups made before its rotation exist.

## Rotate it

Rotate the key when it might have leaked, when someone who held it leaves, or on your regular
schedule. Rotation rewraps the small keys the master key wraps. No credential is re-entered,
nobody re-enrolls, and the controller keeps working throughout.

1. Back up the current key file. It's the *old* key from here on.
2. Generate the new key into a new file, and back that up too:

   ```sh
   openssl rand -base64 32 > /etc/cartio/master-key.new
   chmod 600 /etc/cartio/master-key.new
   ```

3. Point the controller at both keys and restart it. New secrets are wrapped with the new key, and
   the old key is used only to open what it already wrapped:

   ```sh
   export CARTIO_MASTER_KEY_FILE=/etc/cartio/master-key.new
   export CARTIO_PREVIOUS_MASTER_KEY_FILE=/etc/cartio/master-key
   ```

   Set them the same way for every controller process, cron jobs and one-off commands included,
   and make sure no process still runs with only the old key before you go on. A process left on
   the old key alone keeps wrapping new secrets with it.

4. Run the rotation with the same settings, as the app role:

   ```sh
   cartio-controller rotate_master_key --actor "your name"
   ```

   It rewraps each customer's data key in its own transaction and writes a `master_key.rotated`
   event to that customer's audit log, then does the same for engineers' authenticator keys. If
   it stops partway (a crash, a lost connection), run it again: whatever it already rewrapped
   stays rewrapped, and the controller opens both kinds meanwhile. It refuses to start if any key
   is wrapped by neither master key, so a missing third key can't go unnoticed.

5. When it reports that every key is on the new master key, and every process has run with
   both keys since before the rotation started, unset `CARTIO_PREVIOUS_MASTER_KEY_FILE`
   everywhere and restart the controller. If you're unsure whether an old process wrote
   anything, run `rotate_master_key` once more first: it rewraps any stragglers and confirms
   nothing is left. From then on the old key opens nothing in the live database.
6. Keep the old key's backup until every database backup made before step 5 has expired, backups
   taken while the rotation ran included, then destroy it. A backup taken mid-rotation holds keys
   wrapped by both master keys, so restoring it needs both: set the old one as
   `CARTIO_PREVIOUS_MASTER_KEY_FILE` and run `rotate_master_key` again.

If the controller reports that a secret "was wrapped by another master key", the key that wrapped
it isn't configured. The error names the key's id. Restore that key file, or set it as
`CARTIO_PREVIOUS_MASTER_KEY_FILE` and run the rotation.
