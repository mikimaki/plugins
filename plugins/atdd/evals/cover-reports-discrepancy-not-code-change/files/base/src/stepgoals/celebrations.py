from stepgoals import push_gateway


def celebrate(member, steps, goal):
    if steps >= goal:
        push_gateway.send(member, f"Goal reached: {steps:,} steps!")
        return True
    return False
