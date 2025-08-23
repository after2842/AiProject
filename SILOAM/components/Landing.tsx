import React, { useState } from "react";
import { motion } from "framer-motion";
import { useRouter } from "next/navigation";
import Image from "next/image";

/**
 * Minimalistic landing page for a Tax‑Deduction Search website.
 * Technologies: React + TypeScript + TailwindCSS
 */
export default function Landing() {
  const [query, setQuery] = useState("");
  const router = useRouter();

  const handleStartClick = () => {
    router.push("/mainpage");
  };

  const handleLogoClick = () => {
    router.push("/");
  };

  return (
    <motion.div
      initial={{ opacity: 1 }}
      className="min-h-screen flex flex-col bg-white text-gray-800"
    >
      {/* Header */}
      <header className="relative w-full px-6 py-4 flex items-center justify-between">
        <button
          className="px-4 py-2 bg-white rounded-full flex items-center"
          onClick={handleLogoClick}
        >
          <Image
            src="/logo.png" // Replace with your actual logo path
            alt="SILOAM Logo"
            width={120}
            height={120}
            className="mr-2 absolute top-[-30px] left-0"
          />
        </button>
        <nav className="hidden sm:flex space-x-6 text-sm">
          {/* <a href="#features" className="hover:text-blue-600 transition-colors">
            Features
          </a>
          <a href="#about" className="hover:text-blue-600 transition-colors">
            About
          </a>
          <a href="#contact" className="hover:text-blue-600 transition-colors">
            Contact
          </a> */}
        </nav>
      </header>

      {/* Hero Section */}
      <main className="flex-1 flex flex-col items-center justify-center text-center px-6 border border-black">
        <motion.h1
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 1.0 }}
          className="font-bold text-[200px] text-red-500"
        >
          Buy
        </motion.h1>
        <motion.h1
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 1.0, delay: 0.6 }}
          className="font-bold text-[200px] text-red-500"
        >
          <span className="text-pink-600">limitless</span>
        </motion.h1>
        <p className="mt-4 max-w-xl text-gray-600">
          No need to read and type. Just talk to us.
        </p>

        {/* Search Bar */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2, duration: 0.6 }}
          className="mt-8 w-full flex gap-2"
        >
          {/* <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g., $45 at Office Depot for printer ink"
            className="flex-1 px-4 py-3 rounded-lg border focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
            required
          /> */}
          <button
            className="mx-auto px-12 py-3 rounded-full bg-transparent text-pink-800 font-medium hover:bg-gray-200 
           transition border border-pink-900"
            onClick={handleStartClick}
          >
            Start
          </button>
        </motion.div>
      </main>

      {/* Footer */}
      <footer className="w-full py-4 text-center text-xs text-gray-500">
        © {new Date().getFullYear()} TaxFinder Inc. All rights reserved.
      </footer>
    </motion.div>
  );
}
