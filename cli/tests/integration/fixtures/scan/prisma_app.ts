/**
 * Prisma service for IMDB title queries.
 * Used as a fixture for rdst scan integration tests.
 */
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

export async function getRecentMovies() {
  const movies = await prisma.title_basics.findMany({
    where: {
      titletype: "movie",
      startyear: { gt: 2000 },
    },
    orderBy: { startyear: "desc" },
    take: 10,
  });
  return movies;
}

export async function getTopRatedTitles() {
  const ratings = await prisma.title_ratings.findMany({
    where: {
      numvotes: { gt: 1000 },
    },
    orderBy: { averagerating: "desc" },
    take: 25,
  });
  return ratings;
}

export async function getAllMovies() {
  // Anti-pattern: no take/limit on large table
  const allMovies = await prisma.title_basics.findMany({
    where: { titletype: "movie" },
  });
  return allMovies;
}
