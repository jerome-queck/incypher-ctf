import fs from "node:fs";

const request = JSON.parse(fs.readFileSync(0, "utf8"));
if (!request || typeof request !== "object" || Array.isArray(request)) {
  throw new Error("transform request must be an object");
}

let value;
if (request.operation === "from_base64") {
  if (typeof request.value !== "string" || request.value.length > 65536) {
    throw new Error("base64 input is invalid or too large");
  }
  // The package entrypoint's postinstall rewrites unrelated crypto imports. Loading the exact
  // audited operation keeps installation script-free and proves the admitted CyberChef path.
  const { default: FromBase64 } = await import(
    "./node_modules/cyberchef/src/core/operations/FromBase64.mjs"
  );
  const operation = new FromBase64();
  value = Buffer.from(
    operation.run(request.value, ["A-Za-z0-9+/=", true, false]),
  ).toString("utf8");
} else if (request.operation === "deobfuscate") {
  if (typeof request.source !== "string" || request.source.length > 65536) {
    throw new Error("JavaScript input is invalid or too large");
  }
  const { webcrack } = await import("webcrack");
  const result = await webcrack(request.source, {
    deobfuscate: true,
    jsx: false,
    mangle: false,
    unpack: false,
    unminify: true,
  });
  value = result.code;
} else {
  throw new Error("unsupported transform operation");
}

if (typeof value !== "string" || value.length > 262144) {
  throw new Error("transform output is invalid or too large");
}
process.stdout.write(JSON.stringify({ operation: request.operation, value }));
