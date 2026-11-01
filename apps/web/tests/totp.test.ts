import { describe, expect, it } from "vitest";
import { groupSecret, normalizeCode } from "../lib/totp";

describe("apresentação do MFA", () => {
  it("agrupa a chave em blocos de 4", () => {
    expect(groupSecret("AJSPFN6VSWKPZPEO")).toBe("AJSP FN6V SWKP ZPEO");
    expect(groupSecret("ABCDE")).toBe("ABCD E");
  });
  it("normaliza código colado", () => {
    expect(normalizeCode("123 456")).toBe("123456");
    expect(normalizeCode("12a3-45678")).toBe("123456");
  });
});
