import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { DataRefSchema } from "../dataRef";

const FixtureSchema = z.array(
  z.object({
    id: z.string(),
    value: z.unknown(),
    accepted: z.boolean(),
  }),
);

const cases = FixtureSchema.parse(
  JSON.parse(readFileSync(resolve(process.cwd(), "../tests/fixtures/catalog_data_refs.json"), "utf8")),
);

describe("shared catalog data-ref fixture", () => {
  for (const fixture of cases) {
    it(fixture.id, () => {
      expect(DataRefSchema.safeParse(fixture.value).success).toBe(fixture.accepted);
    });
  }
});
