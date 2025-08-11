import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const blog = defineCollection({
	// Load Markdown and MDX files in the `src/content/blog/` directory.
	loader: glob({ base: './src/content/blog', pattern: '**/*.{md,mdx}' }),
	// Type-check frontmatter using a schema
	schema: ({ image }) =>

	    z.object({
	      title: z.string(),
	      description: z.string().optional(),

	      // fechas
	      pubDate: z.coerce.date(),
	      updatedDate: z.coerce.date().optional(),

	      // imágenes: soporta las dos formas
	      // - heroImage -> imágenes locales (importadas con `image()`)
	      // - image { src:string, alt?:string } -> URLs externas (OG, etc.)
	      heroImage: image().optional(),
	      image: z.object({
	        src: z.string(),
	        alt: z.string().optional(),
	      }).optional(),


	      // NUEVO: lo que escribe el robot
	      status: z.enum(['draft','published']).default('draft'),
	      tags: z.array(z.string()).default([]),
	      risk: z.enum(['bajo','medio','alto']).default('bajo'),
	      action: z.string().default(''),
	      sources: z.array(z.object({ name: z.string(), url: z.string().url() })).default([]),

	      featured: z.boolean().default(false),
    }),
});

export const collections = { blog };
