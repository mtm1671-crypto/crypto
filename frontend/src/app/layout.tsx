import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Neuromancy",
  description: "Self-improving AI agent",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="noise">{children}</body>
    </html>
  );
}
