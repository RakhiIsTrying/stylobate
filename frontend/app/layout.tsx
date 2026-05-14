import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Stylobate",
  description: "Multi-agent investing analysis for retail investors.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-foreground antialiased">
        {children}
      </body>
    </html>
  );
}
