// @vitest-environment node
import { afterEach, describe, expect, it } from "vitest";
import { langgraphDeploymentUrl } from "../env.server";

const originalServerUrl = process.env.LANGGRAPH_DEPLOYMENT_URL;
const originalPublicUrl = process.env.NEXT_PUBLIC_LANGGRAPH_URL;

afterEach(() => {
  process.env.LANGGRAPH_DEPLOYMENT_URL = originalServerUrl;
  process.env.NEXT_PUBLIC_LANGGRAPH_URL = originalPublicUrl;
});

describe("langgraphDeploymentUrl", () => {
  it("uses the public deployment URL when the server alias is absent", () => {
    // Given
    delete process.env.LANGGRAPH_DEPLOYMENT_URL;
    process.env.NEXT_PUBLIC_LANGGRAPH_URL = "https://coach.example.test/";

    // When
    const url = langgraphDeploymentUrl();

    // Then
    expect(url).toBe("https://coach.example.test");
  });
});
