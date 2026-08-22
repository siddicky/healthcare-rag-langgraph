"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual style. */
  variant?: "primary" | "secondary" | "gold";
  /** 'sm' uses compact 10x22 padding; default is 16x32. */
  size?: "default" | "sm";
  /** Stretches to fill its container. */
  full?: boolean;
  disabled?: boolean;
  icon?: ReactNode;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  size = "default",
  full = false,
  disabled = false,
  children,
  icon,
  ...rest
}: ButtonProps) {
  const cls = ["btn", `btn-${variant}`, size === "sm" ? "btn-sm" : "", full ? "btn-full" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={cls} disabled={disabled} {...rest}>
      {icon}
      {children}
    </button>
  );
}
