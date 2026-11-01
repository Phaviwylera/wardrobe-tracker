import type { Metadata, Viewport } from "next";
import "@fontsource/rozha-one/400.css";
import "@fontsource/hind/400.css";
import "@fontsource/hind/500.css";
import "@fontsource/hind/600.css";
import "@fontsource/hind/700.css";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "StyleTrail — find where that outfit is sold",
  description: "Upload a photo of an outfit. See every Indian store selling the exact piece, and how its price has moved.",
};

export const viewport: Viewport = { themeColor: "#f5f3f7", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IN">
      <body>
        <div className="shell">
          <Nav />
          <main>{children}</main>
          <footer className="footer small muted">
            Prices are read from each store's page and may change at checkout. StyleTrail isn't affiliated with the
            stores listed.
          </footer>
        </div>
      </body>
    </html>
  );
}
