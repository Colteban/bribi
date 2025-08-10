import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';

export const GET = async () => {
  const posts = await getCollection('blog', ({ data }) => data.status === 'published');
  return rss({
    title: 'Bribi Noticias',
    description: 'Resumen accionable para comercios y pagos en Colombia/LATAM.',
    site: 'https://bribi.co',
    items: posts.map((p) => ({
      title: p.data.title,
      description: p.data.description,
      link: `/blog/${p.slug}/`,
      pubDate: p.data.pubDate,
    })),
  });
};
