const fs = require("fs");
const path = require("path");

const isWindows = process.platform === "win32";

const copyMap = [
  ["share", "*.css", "docs/css"],
  ["share", "*.js", "docs/js"],
  ["share", "*.css", "pcserver/css"],
  ["share", "*.js", "pcserver/js"],
];

const { execSync } = require("child_process");

if (isWindows) {
  execSync(
    `powershell -Command "Copy-Item share\\*.css docs\\css\\ -Force; Copy-Item share\\*.js docs\\js\\ -Force; Copy-Item share\\*.css pcserver\\css\\ -Force; Copy-Item share\\*.js pcserver\\js\\ -Force"`
  );
} else {
  execSync(
    `cp share/*.css docs/css/ && cp share/*.js docs/js/ && cp share/*.css pcserver/css/ && cp share/*.js pcserver/js/`
  );
}