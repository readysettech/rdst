/**
 * Drizzle service for IMDB title queries.
 * Used as a fixture for rdst scan integration tests.
 */
import { eq, gt, desc, count, avg } from "drizzle-orm";
import { db } from "./db";
import { titleBasics, titleRatings } from "./schema";

export async function getRecentMovies() {
  const movies = await db
    .select()
    .from(titleBasics)
    .where(eq(titleBasics.titletype, "movie"))
    .orderBy(desc(titleBasics.startyear))
    .limit(10);
  return movies;
}

export async function getTopRated() {
  const topRated = await db
    .select({
      title: titleBasics.primarytitle,
      rating: titleRatings.averagerating,
      votes: titleRatings.numvotes,
    })
    .from(titleBasics)
    .innerJoin(titleRatings, eq(titleBasics.tconst, titleRatings.tconst))
    .where(gt(titleRatings.numvotes, 1000))
    .orderBy(desc(titleRatings.averagerating))
    .limit(25);
  return topRated;
}

export async function getAllTitles() {
  // Anti-pattern: no limit on large table scan
  const all = await db
    .select()
    .from(titleBasics)
    .where(eq(titleBasics.titletype, "movie"));
  return all;
}
