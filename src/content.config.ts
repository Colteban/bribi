import { defineCollection, z } from "astro:content";

const blog = defineCollection({
  schema: z.object({
    title: z.string(),
    description: z.string().optional(),
    pubDate: z.coerce.date(),
    updatedDate: z.coerce.date().optional(),
    tags: z.array(z.string()).default([]),
    image: z.object({ src: z.string(), alt: z.string().default("") }).optional(),

    // Campos para el robot / curaduría
    status: z.enum(["draft", "published"]).default("draft"),
    risk: z.enum(["bajo", "medio", "alto"]).default("bajo"),
    action: z.string().default(""),
    sources: z.array(z.object({ name: z.string(), url: z.string().url() })).default([]),
  }),
});

export const collections = { blog };
