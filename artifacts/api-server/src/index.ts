import app from "./app";
import { logger } from "./lib/logger";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

const artifactDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const flaskAppPath = path.join(artifactDir, "app.py");
const flask = spawn(
  process.env["PYTHON_BIN"] ?? "python3",
  [
    "-m",
    "gunicorn",
    "app:app",
    "--bind",
    "127.0.0.1:5000",
    "--workers",
    "1",
    "--access-logfile",
    "-",
  ],
  {
  env: {
    ...process.env,
    ...(process.env["SESSION_SECRET"]
      ? { TRACKER_SECRET: process.env["SESSION_SECRET"] }
      : {}),
  },
  cwd: artifactDir,
  stdio: ["ignore", "pipe", "pipe"],
  },
);

flask.stdout.on("data", (chunk: Buffer) => {
  const message = chunk.toString().trim();
  if (message) logger.info({ message }, "Flask backend");
});
flask.stderr.on("data", (chunk: Buffer) => {
  const message = chunk.toString().trim();
  if (message) logger.warn({ message }, "Flask backend");
});
flask.on("error", (error) => {
  logger.error({ err: error }, "Unable to start Flask backend");
});

app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");
});

const shutdown = () => {
  flask.kill("SIGTERM");
  process.exit(0);
};
process.once("SIGTERM", shutdown);
process.once("SIGINT", shutdown);
