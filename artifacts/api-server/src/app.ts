import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();
const flaskBaseUrl = process.env["FLASK_BASE_URL"] ?? "http://127.0.0.1:5000";

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

app.use("/api", async (req, res) => {
  try {
    const headers = new Headers();
    for (const [key, value] of Object.entries(req.headers)) {
      if (key === "host" || key === "content-length" || value === undefined) {
        continue;
      }
      headers.set(key, Array.isArray(value) ? value.join(", ") : value);
    }

    const hasBody = !["GET", "HEAD"].includes(req.method);
    const upstream = await fetch(`${flaskBaseUrl}${req.originalUrl}`, {
      method: req.method,
      headers,
      body: hasBody ? JSON.stringify(req.body ?? {}) : undefined,
    });

    res.status(upstream.status);
    upstream.headers.forEach((value, key) => {
      if (key !== "content-encoding" && key !== "transfer-encoding") {
        res.setHeader(key, value);
      }
    });
    const setCookies = upstream.headers.getSetCookie?.();
    if (setCookies?.length) {
      res.setHeader("set-cookie", setCookies);
    }
    res.send(Buffer.from(await upstream.arrayBuffer()));
  } catch (error) {
    req.log.error({ err: error }, "Flask backend proxy failed");
    res.status(502).json({ error: "backend unavailable" });
  }
});

export default app;
