// Verifies db.config.json can reach PostgreSQL and creates the tables.
const db = require('../src/db');

(async () => {
  const cfg = db.loadConfig();
  console.log(`Connecting to ${cfg.user}@${cfg.host}:${cfg.port}/${cfg.database} ...`);
  try {
    await db.init();
    const s = await db.getAllSettings();
    console.log('OK. Tables ready. Settings keys:', Object.keys(s).join(', '));
    process.exit(0);
  } catch (e) {
    console.error('FAILED:', e.message);
    console.error(`Edit ${db.CONFIG_PATH} with your PostgreSQL credentials and retry.`);
    process.exit(1);
  }
})();
