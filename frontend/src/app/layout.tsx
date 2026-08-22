import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Nymble coach",
  description: "Text-based behavioral coaching for obesity care.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
