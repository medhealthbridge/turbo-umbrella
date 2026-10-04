// Neon project config — applied with `neon deploy`.
//
// Declares the scheduler function and the trigger that runs it every five
// minutes. Postgres is always there; DATABASE_URL is injected into the
// function automatically.
//
// The scheduler needs a GitHub token to start publish runs. It is read from
// your shell when you deploy, so it never lives in this file:
//
//   GITHUB_TOKEN=github_pat_... neon deploy
//
// Use the same fine-grained token as the control panel (this repository only,
// Actions: read and write).

import { defineConfig } from "@neon/config/v1";

export default defineConfig({
  // Managed Better Auth, as in the original setup. The control panel signs in
  // with its own password today; this is provisioned for when it moves to
  // real accounts.
  auth: true,

  functions: {
    scheduler: {
      name: "Autopost scheduler",
      source: "./neon/scheduler.ts",
      env: {
        GITHUB_TOKEN: process.env.GITHUB_TOKEN ?? "",
        GITHUB_REPO: process.env.GITHUB_REPO ?? "medhealthbridge/turbo-umbrella",
        GITHUB_BRANCH: process.env.GITHUB_BRANCH ?? "main",
      },
    },
  },

  triggers: {
    // Five-field cron, always UTC. Every five minutes is plenty: a post goes
    // out within five minutes of its time, and an idle tick is one cheap query.
    "autopost-every-5-min": {
      type: "schedule",
      function: "scheduler",
      cron: "*/5 * * * *",
    },
  },
});
