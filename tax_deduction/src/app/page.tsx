"use client";
import Image from "next/image";
import Landing from "../../components/Landing";
import { motion } from "framer-motion";

export default function Home() {
  return (
    <motion.div
      key="home"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.3 }}
    >
      <Landing />
    </motion.div>
  );
}
