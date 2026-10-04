	moveMemberToTeam: withPermission("team", "update")
		.input(
			z.object({
				memberId: z.string().min(1),
				teamId: z.string().min(1).nullable(),
			}),
		)
		.mutation(async ({ ctx, input }) => {
			const orgId = ctx.session.activeOrganizationId;
			let targetTeam: typeof team.$inferSelect | undefined;
			if (input.teamId) {
				targetTeam = await db.query.team.findFirst({
					where: and(eq(team.id, input.teamId), eq(team.organizationId, orgId)),
				});
				if (!targetTeam) {
					throw new TRPCError({ code: "NOT_FOUND", message: "Team not found" });
				}
			}

			const targetUserId = await db.transaction(async (tx) => {
				// Serialize moves for this member before observing its team memberships.
				const [targetMember] = await tx
					.select({ id: member.id, userId: member.userId })
					.from(member)
					.where(
						and(
							eq(member.id, input.memberId),
							eq(member.organizationId, orgId),
						),
					)
					.for("update");
				if (!targetMember) {
					throw new TRPCError({ code: "NOT_FOUND", message: "Member not found" });
				}

				const currentMemberships = await tx
					.select({ id: teamMember.id, teamId: teamMember.teamId })
					.from(teamMember)
					.innerJoin(team, eq(teamMember.teamId, team.id))
					.where(
						and(
							eq(teamMember.userId, targetMember.userId),
							eq(team.organizationId, orgId),
						),
					);

				for (const membership of currentMemberships) {
					await tx
						.delete(teamMember)
						.where(eq(teamMember.id, membership.id));
					await tx
						.update(team)
						.set({ memberCount: sql`GREATEST(${team.memberCount} - 1, 0)` })
						.where(eq(team.id, membership.teamId));
				}
				if (targetTeam) {
					const [reservedTeam] = await tx
						.update(team)
						.set({ memberCount: sql`${team.memberCount} + 1` })
						.where(
							and(
								eq(team.id, targetTeam.id),
								eq(team.organizationId, orgId),
								sql`${team.memberCount} < ${team.maxMembers}`,
							),
						)
						.returning({ id: team.id });

					if (!reservedTeam) {
						throw new TRPCError({
							code: "BAD_REQUEST",
							message: "Team member limit reached",
						});
					}

					await tx.insert(teamMember).values({
						id: nanoid(),
						teamId: targetTeam.id,
						userId: targetMember.userId,
						membershipKey: `${targetTeam.id}:${targetMember.userId}`,
						createdAt: new Date(),
					});
				}
				await tx
					.update(member)
					.set({ teamId: targetTeam?.id ?? null })
					.where(eq(member.id, targetMember.id));
				return targetMember.userId;
			});

			await audit(ctx, {
				action: "update",
				resourceType: "user",
				resourceId: targetUserId,
				metadata: {
					type: "moveMemberToTeam",
					teamId: targetTeam?.id ?? null,
				},
			});
			return true;
		}),
